"""Заглушка модуля экологии (инженерно-экологические изыскания) —
4-й модуль, Модель 1 методики (объём × расценка), как геология/
геофизика/геодезия — ничего нового в core/models.py/aggregator.py не
требуется.

**Справочник расценок.** Ни один документ заказчика не называет
сборник для экологии явно (в отличие от геологии — СБЦ-99, геофизики
— СЦиР-1982, геодезии — СБЦи5.6). Источник найден по факту структуры
реальной сметы, не угадан: `reference_data/Карьер_эталон.xlsx`, лист "№2.5_ИЭИ" — цитирует "Таблица N, п.M"/"Таблица N,
§M" ТОГО ЖЕ сборника СБЦ-99, что и геология (не отдельный сборник для
экологии), включая совпадающий индекс пересчёта цен (81,95 на III
квартал 2026 г. — см. data/build_references.py::ECOLOGY_INDEX_ROW).

**Состав позиций и охват модуля.** Реальная ведомость объёмов
заказчика (документ "смета стороннего объекта Г", лист "Объем и
стоимость работ", раздел "Инженерно-экологические изыскания", строки
91–109) даёт целевой состав из 8 верхнеуровневых позиций
(радиационное обследование, радон, физические факторы, группа
"Опробование окружающей среды" из 11 подпозиций, получение исходных
данных, программа работ, камеральная обработка материалов,
технический отчёт) — но там только ОБЪЁМЫ и итоговая сумма раздела,
без цены за единицу. Цена за единицу берётся из ДРУГОГО документа (эталонная
смета "№2.5_ИЭИ" выше) — та же схема, что уже использовалась для
геологии (эталон цены — один объект, реалистичный состав ввода —
другой, см. docs/predmetnaya_oblast.md).

**Покрытие:** 8 из 8 верхнеуровневых позиций реализовано (5 с ценой,
2 без цены, 1 составная — группа опробования); в группе "Опробование
окружающей среды" 11 из 11 подпозиций реализовано, из них 9 с ценой,
2 без цены. Программа работ, камеральная обработка материалов и
технический отчёт добавлены 2026-09-24 (см. ниже и
записи разработки). Без цены — сознательно,
не угадано:
- "Измерение физических факторов (шум, ЭМИ)" — нет ни в эталонной
  смете, ни в какой-либо другой найденной смете заказчика позиции с
  этим названием и ценой из СБЦ-99 (в "№2.5_ИЭИ" есть похожие строки
  55-57, но с пометкой "Работы, не учтенные СБЦ-99" — то есть это
  сознательно НЕ табличная расценка, а прайс сторонней лаборатории —
  не тот же источник, что остальные позиции, не смешиваем).
- "Пробы почв на химические загрязнители (расширенный анализ)" и
  "Тест грунта на естественные радионуклиды" — нет отдельного кода в
  эталонной смете (есть только обычный анализ почв и ЕРН почв, но не
  их "расширенный"/"грунтовый" варианты).
- "Получение исходных данных (в т.ч. платных)" — в эталонной смете
  есть похожий раздел ("Работы, не учтенные СБЦ-99": запросы в
  ФГБУ УГМС, службу ветеринарии, ТФГИ), но это фиксированные суммы за
  КОНКРЕТНЫЙ запрос (прайс, не расценка за единицу измерения),
  из другого дела заказчика "в т.ч. платных" не следует единой цены
  за штуку.

Для всех непокрытых позиций — тот же механизм `get_price_or_missing` (core/module_helpers.py),
что и в остальных модулях: строка всё равно строится (price=0,
говорящий basis), а не роняет расчёт. См. docs/known_risks.md.

**Программа работ, камеральная обработка, технический отчёт.**
Формулы сверены до копейки на двух эталонах нового шаблона сметы —
"№2.5_ИЭИ" (Карьер) и "№1.4_ИЭИ" (Автодорога):
- программа работ = 1 × Т81 п.1 (200) × 1,25 (примечание 1) — от
  ввода не зависит;
- камеральная обработка = 6 камеральных пар полевых строк
  (рекогносцировка, почвенная рекогносцировка, маршрутные наблюдения,
  описание точек, радиационное обследование, радон — объём = объём
  полевой строки, цена — камеральная графа того же пункта СБЦ-99) +
  0,2 × ИТОГО лабораторных работ (Т86 п.6);
- технический отчёт = 0,21 (Т87 п.1) × (предполевые + камеральные);
  предполевые = программа работ + сбор и анализ фондовых материалов
  (Т78 п.2) + подготовка электронной подосновы (Т80, пункт п.13/п.14
  выбирает оператор — основание выбора из эталонов не выводится).

Чтобы у камеральных пар был объём, добавлены полевые строки
рекогносцировки, почвенной рекогносцировки, маршрутных наблюдений и
описания точек (Т9/Т10/Т11 — в эталонной смете есть, в ведомости
объекта А нет). Выбор пункта — по проходимости: хорошая → Т9 п.1,
Т9 п.4, Т10 п.3; удовлетворительная → Т9 п.2, Т9 п.5, Т10 п.2.

**Лабораторный блок (этап III, с 30.10.2026 — Э6г карты).** Анализы
т.70 §62/§63/§66, т.71 §1, т.70 §14+22+32+38, т.64 §12, т.91 §4, т.72 —
повторяющаяся группа «вид анализа + количество» (12 видов — все строки
лабораторного раздела обоих эталонов, `LAB_ANALYSES`). Каждый вид —
строка сметы `ecology.lab.<вид>`; их сумма = «ИТОГО лабораторных»,
камеральная обработка лабораторных — строка «доля от базы» 0,2 × эти
строки, районный к камеральным берёт их в базу (как H86 листа).
Прежний ввод «Итого лабораторные суммой» (`lab_total_base`) сохранён —
для старых версий и как запасной путь: работает, только если группа
анализов пуста (тогда стоимости лабораторных в смете нет, как раньше);
если заполнены оба — считается по строкам анализов, сумма не
используется, предупреждение.

Упрощения относительно эталона (см. docs/known_risks.md):
- пункт Т80 для подготовки электронной подосновы выбирает оператор;
- коэффициент камеральной обработки маршрутных наблюдений не выведен
  однозначно — оператор задаёт его сам, по умолчанию 1.

Радиационное обследование и радон — отдельные строки вне
лабораторного блока (как в эталонах нового шаблона), в базу 20% не
входят."""

from dataclasses import dataclass, field as dataclass_field
from typing import Any

from core import param_rules
from core.accruals import (
    EXTERNAL_TRANSPORT,
    ORG_LIQUIDATION,
    REGIONAL_FIELD,
    REGIONAL_OFFICE,
    AccrualScheme,
    accrual_warnings,
    apply_accruals,
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
from core.stages import SOURCE_CLASS_A, SOURCE_INPUT, SOURCE_OPERATOR, Stage, VolumeLine
from core.tz import KIND_ESTIMATE, OBJECT_LINEAR, Proposal, TzParams
from modules.ecology import key_volumes

SOURCE = "СБЦ-99"

NOT_A_RATED_POSITION_PREFIX = "Не найдена как расценка СБЦ-99"

RADIATION_TABLE = "92"
RADIATION_ITEM = "3"
RADON_TABLE = "91"
RADON_ITEM = "1"
FIELD_CATEGORY = "field"
KAMERAL_CATEGORY = "kameral"

TRANSPORT_TABLE = "4"
TRANSPORT_ITEM = "4"

# Предполевые: составление программы работ. Коэффициент 1,25
# ("примечание 1") — одинаковый на обоих эталонах, вручную сверенная
# константа (тот же принцип, что VEZ_COEFFICIENT геофизики).
PROGRAM_TABLE = "81"
PROGRAM_ITEM = "1"
PROGRAM_COEFFICIENT = 1.25

# Предполевые: сбор и анализ фондовых материалов о состоянии природной
# среды — Т78 п.2 (II категория сложности, измеритель — 10 цифровых
# значений), одинаково на обоих эталонах.
FUND_MATERIALS_TABLE = "78"
FUND_MATERIALS_ITEM = "2"

# Предполевые: подготовка электронной подосновы — Т80, но пункт на
# эталонах разный (Карьер п.14 — 10,02; Автодорога п.13 — 12,29) при
# одинаковой "II категории сложности" в наименовании. Основание выбора
# из листов не выводится (на остальных найденных листах ИЭИ этой
# позиции нет вовсе) — пункт выбирает оператор, открытый вопрос в
# docs/known_risks.md. Порядок — по номеру пункта, не "рекомендация".
SUBBASE_TABLE = "80"
SUBBASE_ITEM_CHOICES = ["13", "14"]
SUBBASE_ITEM_EXAMPLES = {"13": "как на эталоне «Автодорога»", "14": "как на эталоне «Карьер»"}
SUBBASE_ITEM_DEFAULT = "13"

# Рекогносцировка: коэффициент 1,1 ("примечание 1" к Т9) — и у
# полевой, и у камеральной строки на обоих эталонах. У камеральной он
# ФИКСИРОВАННЫЙ, не копия полевого: на других найденных листах шаблона
# при полевом 1,25 камеральный всё равно 1,1 (см.
# записи разработки).
RECON_COEFFICIENT = 1.1

# Проходимость -> пункты СБЦ-99 для рекогносцировки / почвенной
# рекогносцировки / маршрутных наблюдений. Нумерация Т10 (п.3 —
# хорошая, п.2 — удовлетворительная) взята как есть из наименований
# строк эталонов, не переставлена.
PASSABILITY_CHOICES = [
    ("good", "Хорошая"),
    ("satisfactory", "Удовлетворительная"),
]
PASSABILITY_DEFAULT = "good"
PASSABILITY_CODES: dict[str, dict[str, tuple[str, str]]] = {
    "good": {"recon": ("9", "1"), "soil_recon": ("9", "4"), "route": ("10", "3")},
    "satisfactory": {"recon": ("9", "2"), "soil_recon": ("9", "5"), "route": ("10", "2")},
}
OBSERVATION_POINTS_TABLE = "11"
OBSERVATION_POINTS_ITEM = "2"

LAB_KAMERAL_TABLE = "86"
LAB_KAMERAL_ITEM = "6"
REPORT_TABLE = "87"
REPORT_ITEM = "1"

VK_RADIATION = "radiation_area"
VK_RADON = "radon_points"
VK_PHYSICAL_FACTORS = "physical_factors_points"
VK_DATA_ACQUISITION = "data_acquisition_count"
VK_SAMPLING = "sampling"
SAMPLING_VOLUME_KEY_PREFIX = "sampling_"
VK_RECON_LENGTH = "recon_length_km"
VK_PASSABILITY = "passability"
VK_OBSERVATION_POINTS = "observation_points"
VK_ROUTE_FIELD_COEF = "route_field_coefficient"
VK_ROUTE_KAMERAL_COEF = "route_kameral_coefficient"
VK_LAB_TOTAL = "lab_total_base"
VK_FUND_MATERIALS = "fund_materials_count"
VK_SUBBASE_ITEM = "subbase_item"
VK_SUBBASE_VOLUME = "subbase_volume"
# Множитель числа элементов у тяжёлых металлов в донных отложениях
# (параметр этапа сметы, см. LAB_ANALYSES и SEDIMENT_ELEMENTS_SHEET ниже).
VK_SEDIMENT_ELEMENTS = "heavy_metals_sediments_elements"

# --- многоэтапный сценарий (core/stages.py) ---
#
# Ключи строк ведомости, которых нет среди полей ввода: позиции,
# генерируемые по жёсткой связи. build_estimate читает их, если они
# есть во входе (многоэтапный сценарий — правка оператора в
# ведомости доходит до сметы), и берёт прежнее значение, если их нет
# (одноэтапный вызов build_volumes → build_estimate — числа те же).
VK_PROGRAM = "program"
VK_SOIL_RECON_LENGTH = "soil_recon_length_km"
VK_ROUTE_LENGTH = "route_length_km"
VK_REPORT = "report"
KAMERAL_VOLUME_KEY_PREFIX = "kameral_"
# Камеральная пара -> ключ полевой строки ведомости, от которой она
# берёт объём (допущение нового шаблона сметы: объём камеральной строки
# = объём полевой, см. build_estimate).
KAMERAL_PAIRS: dict[str, str] = {
    "recon": VK_RECON_LENGTH,
    "soil_recon": VK_SOIL_RECON_LENGTH,
    "route": VK_ROUTE_LENGTH,
    "observation_points": VK_OBSERVATION_POINTS,
    "radiation": VK_RADIATION,
    "radon": VK_RADON,
}

# Параметры сметы — ввод ЭТАПА СМЕТЫ, не ведомости (решение №12,
# docs/multistage_design.md: ведомость — "что и сколько делать", смета —
# "как считать стоимость"). Выбор пункта Т80 и проходимость выбирают
# только пункт расценки, коэффициенты — множители цены, итог
# лабораторных — денежная база камеральной строки 0,2 × итого.
ESTIMATE_PARAMETER_FIELDS = (
    VK_SUBBASE_ITEM,
    VK_PASSABILITY,
    VK_ROUTE_FIELD_COEF,
    VK_ROUTE_KAMERAL_COEF,
    VK_LAB_TOTAL,
    VK_SEDIMENT_ELEMENTS,
)

# Строка сметы -> строка ведомости, от которой она берёт объём.
# Без строки ведомости: транспорт (доля) и камеральная обработка
# лабораторных (её "объём" — денежный итог лабораторных, параметр
# этапа сметы).
ESTIMATE_TO_VOLUME_KEY: dict[str, str] = {
    "ecology.program": VK_PROGRAM,
    "ecology.fund_materials": VK_FUND_MATERIALS,
    "ecology.subbase": VK_SUBBASE_VOLUME,
    "ecology.recon": VK_RECON_LENGTH,
    "ecology.soil_recon": VK_SOIL_RECON_LENGTH,
    "ecology.route": VK_ROUTE_LENGTH,
    "ecology.observation_points": VK_OBSERVATION_POINTS,
    "ecology.radiation": VK_RADIATION,
    "ecology.radon": VK_RADON,
    "ecology.physical_factors": VK_PHYSICAL_FACTORS,
    "ecology.data_acquisition": VK_DATA_ACQUISITION,
    "ecology.report": VK_REPORT,
    **{f"ecology.kameral.{pair}": f"{KAMERAL_VOLUME_KEY_PREFIX}{pair}" for pair in KAMERAL_PAIRS},
}


def volume_key_for_estimate_line(line_id: str) -> str | None:
    if line_id.startswith("ecology.sampling."):
        return f"{SAMPLING_VOLUME_KEY_PREFIX}{line_id[len('ecology.sampling.'):]}"
    if line_id.startswith("ecology.lab."):
        return f"lab_analysis_{line_id[len('ecology.lab.'):]}"
    return ESTIMATE_TO_VOLUME_KEY.get(line_id)

# Нейтральный множитель для полей-коэффициентов, которые оператор не
# задавал: 1 не меняет строку. 0 или "не применять" (None) здесь не
# годятся — 0 обнулил бы строку, а None пришлось бы отдельно
# обрабатывать в calculate_amount.
NEUTRAL_COEFFICIENT = 1.0

# Стандартный набор видов опробования из реальной ведомости объёмов
# заказчика (объект А) — фиксированный список (как масштабный ряд
# геодезии), не производный от справочника: справочник даёт цену на
# КОД расценки, а не на "вид пробы" как таковой — сопоставление вид
# пробы -> код расценки вручную сверено ниже, см. SAMPLE_TYPE_PRICE_CODES.
SAMPLE_TYPE_CHOICES = [
    ("soil_chemical", "Пробы почв на химические загрязнители"),
    ("soil_chemical_extended", "Пробы почв на химические загрязнители (расширенный анализ)"),
    ("soil_agrochemical", "Пробы почвы на агрохимические показатели"),
    ("soil_radionuclides", "Пробы почв на естественные радионуклиды"),
    ("soil_sanitary", "Пробы почв на санитарно-эпидемиологические показатели"),
    ("subsoil_chemical", "Пробы грунта на химические загрязнители"),
    ("subsoil_radionuclides", "Тест грунта на естественные радионуклиды"),
    ("surface_water_chemical", "Пробы поверхностной воды на химические загрязнители"),
    ("groundwater_chemical", "Пробы подземной воды на химические загрязнители"),
    ("bottom_sediments_chemical", "Пробы донных отложений на химические загрязнители"),
    ("background_samples", "Фоновые пробы"),
]
SAMPLE_TYPE_LABELS: dict[str, str] = dict(SAMPLE_TYPE_CHOICES)

# code -> (таблица, пункт, коэффициент). Коэффициент — вручную сверенная
# константа (тот же принцип, что VEZ_COEFFICIENT геофизики/K1,K2
# геодезии): в эталонной смете он либо указан прямо в строке позиции
# (0.9 у "Таблица 60, п.7, прим. 1"), либо отсутствует (коэффициент=1).
# Все цены — Таблица 60 эталонной сметы "№2.5_ИЭИ": одна и та же
# позиция справочника (п.7) переиспользуется для нескольких видов проб
# (обычная химия/фоновая — к=0.9, ЕРН — к=1.2) — это не совпадение цен
# разных позиций, а буквально ОДНА позиция справочника с разным
# коэффициентом в разных строках сметы.
SAMPLE_TYPE_PRICE_CODES: dict[str, tuple[str, str, float]] = {
    "soil_chemical": ("60", "7", 0.9),
    "soil_agrochemical": ("60", "6", 1.0),
    "soil_radionuclides": ("60", "7", 1.2),
    "soil_sanitary": ("60", "10", 1.0),
    "subsoil_chemical": ("60", "7", 0.9),
    "surface_water_chemical": ("60", "1", 1.0),
    "groundwater_chemical": ("60", "2", 1.0),
    "bottom_sediments_chemical": ("60", "5", 1.0),
    "background_samples": ("60", "7", 0.9),
    # Биотестирование (Э6г, 30.10.2026): строки «Отбор проб почв/грунтов
    # на биотестирование» обоих эталонов (№2.5_ИЭИ!41–42, №1.4_ИЭИ!43–44) —
    # Таблица 60, п.7 без примечания, коэффициента нет.
    "soil_biotest": ("60", "7", 1.0),
    "subsoil_biotest": ("60", "7", 1.0),
    # soil_chemical_extended, subsoil_radionuclides — сознательно нет,
    # см. докстринг модуля выше.
}
# Добавлены после списка ведомости Объект «Тест» (в нём их нет) — по
# строкам эталонов; в конце списка, чтобы порядок прежних не менялся.
SAMPLE_TYPE_CHOICES += [
    ("soil_biotest", "Пробы почв на биотестирование"),
    ("subsoil_biotest", "Пробы грунтов на биотестирование"),
]
SAMPLE_TYPE_LABELS.update(dict(SAMPLE_TYPE_CHOICES))


# --- Лабораторный блок (этап III эталонов, Э6г карты, 30.10.2026) ---
#
# Виды анализов — строки раздела «III этап – Лабораторные работы» обоих
# эталонов: «№2.5_ИЭИ» (Карьер, строки 58–69) и «№1.4_ИЭИ» (Автодорога,
# строки 60–71) — набор и формулы одинаковые. Оператор вводит вид и
# количество (образцов / определений / проб — единица в подписи).
VK_LAB_ANALYSES = "lab_analyses"
LAB_VOLUME_KEY_PREFIX = "lab_analysis_"
LAB_LINE_PREFIX = "ecology.lab."


@dataclass(frozen=True)
class LabAnalysis:
    key: str
    label: str
    unit: str
    table: str
    item: str
    # Множители строки (как в формуле ячейки листа).
    coefs: dict = dataclass_field(default_factory=dict)
    # ROUND(…;0) в листе — у части строк эталона он есть, у части нет.
    rounding: int | None = None
    # Составная цена (сумма нескольких параграфов таблицы в одной ячейке
    # листа) — парсер справочника такие цены не выносит (одна позиция
    # справочника = один пункт). Тогда цена — значение ячейки эталона,
    # источник — в `fixed_basis`.
    fixed_price: float | None = None
    fixed_basis: str = ""


LAB_ANALYSES: tuple[LabAnalysis, ...] = (
    # №2.5_ИЭИ!H58 = PRODUCT(D58;E58;7), №1.4_ИЭИ!H60 — цена т.70 §62 за
    # один элемент × 7 элементов (свинец, кадмий, никель, цинк, медь,
    # мышьяк, ртуть — в наименовании строки «итого 7 химических элементов»).
    LabAnalysis("heavy_metals_soil",
                "Химические элементы (7 тяжёлых металлов) в почвах и грунтах", "образец",
                "70", "62", {"k_elements": 7}),
    # №2.5_ИЭИ!H59 = PRODUCT(D59;E59), №1.4_ИЭИ!H61 — для донных отложений
    # в обоих листах БЕЗ ×7 (хотя в колонке «расчёт» написано ×7). Повторено
    # как в листе, вопрос пользователю — список вопросов В-38.
    # По смыслу расценки (т.70 §62 — цена за ОДИН элемент, а в наименовании
    # строки те же 7 элементов, что у почв) должно быть ×7: сейчас цена за
    # образец в 7 раз меньше, чем у почв — вероятно, ошибка шаблона (список вопросов В-38).
    # Пока нет ответа — по умолчанию как в листе (без ×7), множитель —
    # параметр этапа сметы VK_SEDIMENT_ELEMENTS, check() предупреждает.
    LabAnalysis("heavy_metals_sediments",
                "Химические элементы (тяжёлые металлы) в донных отложениях", "образец",
                "70", "62"),
    LabAnalysis("pah_soil", "Бенз(а)пирен (ПАУ) в почвах и грунтах", "образец",
                "70", "66", rounding=0),
    LabAnalysis("pah_sediments", "Бенз(а)пирен (ПАУ) в донных отложениях", "образец",
                "70", "66", rounding=0),
    LabAnalysis("oil_soil", "Нефтяные углеводороды в почвах и грунтах", "образец",
                "70", "63", rounding=0),
    LabAnalysis("oil_sediments", "Нефтяные углеводороды в донных отложениях", "образец",
                "70", "63", rounding=0),
    LabAnalysis("water_extract", "Анализ водной вытяжки", "образец", "71", "1", rounding=0),
    # Ошибка в листе (справочно, на расчёт модуля не влияет): №1.4_ИЭИ!H67 =
    # ROUND(PRODUCT(D66;E67);0) и №2.5_ИЭИ!H65 = ROUND(PRODUCT(D64;E65);0) —
    # количество берётся из строки ВОДНОЙ ВЫТЯЖКИ, а не своей. Модуль берёт
    # своё количество (правильно); эталонный тест сходится лишь потому, что
    # в листах D66 = D67 (Автодорога, обе = D39) и D64 = D65 = 0 (Карьер).
    # См. docs/etalon.md, раздел экологии.
    LabAnalysis("agrochemical", "Агрохимические показатели", "образец", "70", "14+22+32+38",
                rounding=0, fixed_price=41.9,
                fixed_basis="т.70 §14, 22, 32, 38 — сумма параграфов 2 + 7,6 + 14,2 + 18,1 = "
                "41,9 (формула ячейки эталона №2.5_ИЭИ!E65 = №1.4_ИЭИ!E67; составная цена, "
                "в справочник не вынесена)"),
    LabAnalysis("granulometric", "Гранулометрический анализ почвогрунтов", "образец",
                "64", "12", rounding=0),
    LabAnalysis("gamma_spectrometry", "Гамма-спектрометрия проб (ЕРН)", "определение",
                "91", "4"),
    LabAnalysis("surface_water", "Химический состав поверхностной воды (33 показателя)", "проба",
                "72", "состав поверхностной воды", fixed_price=218.8,
                fixed_basis="Таблица 72, § 2, 7, 8, 12, 15, 21, 24, 30, 33, 35, 38, 39, 41, 42, "
                "43, 48, 49, 51, 54, 56, 66, 69, 70, 73, 74, 75, 78, 79, 81, 82, 84, 85, 90 — "
                "сумма 218,8 (ячейка эталона №2.5_ИЭИ!E68 = №1.4_ИЭИ!E70; составная цена, "
                "в справочник не вынесена)"),
    LabAnalysis("groundwater", "Химический состав подземной воды (24 показателя)", "проба",
                "72", "состав подземной воды", fixed_price=154.0,
                fixed_basis="Таблица 72, § 2, 8, 12, 15, 21, 24, 30, 33, 35, 38, 41, 42, 43, 48, "
                "49, 54, 56, 66, 73, 75, 78, 79, 90 — сумма 154 (ячейка эталона №2.5_ИЭИ!E69 = "
                "№1.4_ИЭИ!E71; составная цена, в справочник не вынесена)"),
)
LAB_ANALYSES_BY_KEY: dict[str, LabAnalysis] = {a.key: a for a in LAB_ANALYSES}
LAB_ANALYSIS_CHOICES = [(a.key, f"{a.label} (за {a.unit})") for a in LAB_ANALYSES]

# Отметка в основании камеральной строки лабораторных: введены и строки
# анализов, и итог суммой — посчитано по строкам (check() предупреждает).
LAB_TOTAL_IGNORED_MARK = "итог лабораторных, введённый суммой, не использован"

# Тяжёлые металлы в донных отложениях: множитель числа элементов (параметр
# этапа сметы). По умолчанию 1 — как в эталонных листах (№2.5_ИЭИ!H59,
# №1.4_ИЭИ!H61 = PRODUCT(D;E) без ×7), числа прежние. 7 — по смыслу
# расценки (вопрос пользователю В-38).
SEDIMENT_ELEMENTS_SHEET = 1.0


def rule_default(name: str) -> float:
    """Множитель/коэффициент этапа сметы по умолчанию — справочник правил параметров
    (core/param_rules.py, data/pravila_parametrov.json; сейчас 1)."""
    return float(param_rules.default("ecology", name, NEUTRAL_COEFFICIENT))
SEDIMENT_ELEMENTS_BY_MEANING = 7

# Связь «отбор проб → лабораторный анализ» в эталонных листах: количество
# анализа — формула от числа отобранных проб (ячейки — №1.4_ИЭИ
# Автодороги; в №2.5_ИЭИ Карьера те же связи на 2 строки выше, кроме водной
# вытяжки D64 = 0 и подземной воды D69 = 3 — числа, вписанные вручную, равные
# отбору). Автоподстановки нет — только предупреждение, если пробы
# отобраны, а анализ не введён (лаборатория без анализа — вероятно, забыли).
# Внимание: у проб почв «количество» модуля может быть числом точечных
# проб (лист Автодороги: 7 проб × 5 точечных = 35), а анализ — по числу
# объединённых проб (21 = 7 + 14), поэтому равенство чисел не проверяется.
LAB_SAMPLE_LINKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("heavy_metals_soil", ("soil_chemical", "subsoil_chemical"), "D60 = D35 + D37"),
    ("pah_soil", ("soil_chemical", "subsoil_chemical"), "D62 = D35 + D37"),
    ("oil_soil", ("soil_chemical", "subsoil_chemical"), "D64 = D35 + D37"),
    ("heavy_metals_sediments", ("bottom_sediments_chemical",), "D61 = D48"),
    ("pah_sediments", ("bottom_sediments_chemical",), "D63 = D48"),
    ("oil_sediments", ("bottom_sediments_chemical",), "D65 = D48"),
    ("water_extract", ("soil_agrochemical",), "D66 = D39"),
    ("agrochemical", ("soil_agrochemical",), "D67 = D39"),
    ("granulometric", ("soil_chemical", "subsoil_chemical", "soil_agrochemical"),
     "D68 = D35 + D37 + D39"),
    ("gamma_spectrometry", ("soil_radionuclides",), "D69 = D42"),
    ("surface_water", ("surface_water_chemical",), "D70 = D47"),
    ("groundwater", ("groundwater_chemical",), "D71 = D49"),
)


def lab_link_warnings(sample_counts: dict[str, float], lab_counts: dict[str, float]) -> list[str]:
    """Предупреждения «пробы отобраны, анализ не введён» по LAB_SAMPLE_LINKS.

    sample_counts / lab_counts — {вид пробы / вид анализа: количество}."""
    warnings = []
    for analysis_key, sample_keys, cells in LAB_SAMPLE_LINKS:
        if lab_counts.get(analysis_key):
            continue
        taken = [(k, sample_counts[k]) for k in sample_keys if sample_counts.get(k)]
        if not taken:
            continue
        samples = ", ".join(f"«{SAMPLE_TYPE_LABELS[k]}» ({n:g} шт)" for k, n in taken)
        warnings.append(
            f"Отобраны пробы {samples}, но анализ «{LAB_ANALYSES_BY_KEY[analysis_key].label}» "
            f"не введён — в эталонной смете количество этого анализа берётся из числа "
            f"отобранных проб (№1.4_ИЭИ!{cells}). Добавьте его в группу «Лабораторные "
            "анализы» на ведомости или убедитесь, что он не нужен."
        )
    return warnings


def _group_counts(rows: Any, type_field: str, count_field: str, prefix: str) -> dict[str, float]:
    """Сумма количеств строк группы по виду: {prefix + вид: количество}.

    Строки без вида или с нулевым количеством пропускаются: форма
    интерфейса показывает у группы пустую строку-заглушку (первый вид,
    количество 0) — она не должна переключать камеральную обработку
    лабораторных с суммы, введённой оператором, на строки анализов."""
    counts: dict[str, float] = {}
    for row in rows or []:
        kind = row.get(type_field)
        count = row.get(count_field, 0) or 0
        if not kind or not count:
            continue
        key = f"{prefix}{kind}"
        counts[key] = counts.get(key, 0) + count
    return counts

# Начисления уровня сметы (С3, core/accruals.py) — по формулам эталонных
# листов «№1.4_ИЭИ» (Автодорога, строки 53–95) и «№2.5_ИЭИ» (Карьер):
#   H54 внешний транспорт = ROUND((полевые + вн.тр.) × ставка) (в эталоне 39,2 %);
#   H55 оргликвидация = ROUND((полевые + вн.тр.) × 6 %) — без ×2,5;
#   H90 полевые × (1,35+0,25): надбавка 0,6 от (полевые + вн.тр. + внеш.тр. + оргликв.),
#       предполевые не входят;
#   H93 (предполевые + лабораторные + камеральные + техотчёт) × (1,1+0,15): надбавка
#       0,25. Лабораторные — строки ecology.lab.* (с Э6г); если лабораторные введены
#       только суммой (запасной путь, строк нет) — в базу они не попадают, как раньше.
# Районные — без округления (как в листе).
_ECOLOGY_FIELD = ("ecology.recon", "ecology.soil_recon", "ecology.route",
                  "ecology.observation_points", "ecology.radiation", "ecology.radon",
                  "ecology.physical_factors")
_ECOLOGY_PREFIELD = ("ecology.program", "ecology.fund_materials", "ecology.subbase",
                     "ecology.data_acquisition")
ACCRUALS = AccrualScheme(
    module="ecology",
    groups={
        "field": lambda i: i in _ECOLOGY_FIELD or i.startswith("ecology.sampling."),
        "prefield": lambda i: i in _ECOLOGY_PREFIELD,
        "lab": lambda i: i.startswith("ecology.lab."),
        "office": lambda i: i.startswith("ecology.kameral.") or i == "ecology.report",
        "internal_transport": lambda i: i == "ecology.transport",
    },
    bases={
        EXTERNAL_TRANSPORT: ("field", "internal_transport"),
        ORG_LIQUIDATION: ("field", "internal_transport"),
        REGIONAL_FIELD: ("field", "internal_transport", EXTERNAL_TRANSPORT, ORG_LIQUIDATION),
        REGIONAL_OFFICE: ("prefield", "lab", "office"),
    },
    rounding={EXTERNAL_TRANSPORT: 0, ORG_LIQUIDATION: 0},
    sources={
        EXTERNAL_TRANSPORT: "т.5 п.6 (эталон №1.4_ИЭИ!H54)",
        ORG_LIQUIDATION: "п.13 ОУ (эталон №1.4_ИЭИ!H55)",
        REGIONAL_FIELD: "т.3 п.7, п.8е ОУ (эталон №1.4_ИЭИ!H90)",
        REGIONAL_OFFICE: "т.3 п.3, п.8е ОУ (эталон №1.4_ИЭИ!H93)",
    },
)



# Находка ревью №10 (30.10.2026): объёмы, которые по смыслу работ и эталонам
# должны быть > 0, когда раздел не пустой, — предупреждение этапа ведомости
# (core/module_helpers.py::zero_volume_warnings; список — список вопросов В-8).
VOLUME_EXPECTED_NONZERO = {"recon_length_km": "Протяжённость рекогносцировочного обследования, км", "observation_points": "Число точек наблюдений"}
VOLUME_EXPECTED_NONZERO_IN_ROWS = {"sampling": ("sample_count", "Количество проб")}


def _volume_zero_warnings(inputs, previous, refs):
    return [
        *zero_volume_warnings(inputs, VOLUME_EXPECTED_NONZERO, VOLUME_EXPECTED_NONZERO_IN_ROWS),
        *lab_link_warnings(
            _group_counts(inputs.get(VK_SAMPLING), "sample_type", "sample_count", ""),
            _group_counts(inputs.get(VK_LAB_ANALYSES), "analysis_type", "analysis_count", ""),
        ),
    ]

class EcologyModule:
    code = "ecology"

    def input_schema(self) -> list[Field]:
        return [
            Field(
                name=VK_FUND_MATERIALS,
                label="Сбор и анализ фондовых материалов о состоянии природной среды "
                "(измеритель — 10 цифровых значений)",
                type="number",
                required=False,
            ),
            Field(
                name=VK_SUBBASE_ITEM,
                label="Подготовка электронной подосновы — пункт Т80 СБЦ-99",
                type="text",
                required=False,
                dynamic_options=True,
            ),
            Field(
                name=VK_SUBBASE_VOLUME,
                label="Подготовка электронной подосновы (схема размещения объектов), привязка — объём",
                type="number",
                required=False,
            ),
            Field(
                name=VK_PASSABILITY,
                label="Проходимость территории (рекогносцировка, маршрутные наблюдения)",
                type="text",
                required=False,
                dynamic_options=True,
            ),
            Field(
                name=VK_RECON_LENGTH,
                label="Протяжённость рекогносцировочного обследования, км",
                type="number",
                required=False,
            ),
            Field(
                name=VK_OBSERVATION_POINTS,
                label="Число точек наблюдений (описание при составлении инж.-экологической карты)",
                type="number",
                required=False,
                integer=True,
            ),
            Field(
                name=VK_ROUTE_FIELD_COEF,
                label="Коэффициент к полевым маршрутным наблюдениям (примечание к Т10)",
                type="number",
                required=False,
                default=rule_default(VK_ROUTE_FIELD_COEF),
            ),
            Field(
                name=VK_RADIATION,
                label="Площадь радиационного обследования, га",
                type="number",
                required=True,
            ),
            Field(
                name=VK_RADON,
                label="Число точек измерения плотности потока радона",
                type="number",
                required=True,
                integer=True,
            ),
            Field(
                name=VK_PHYSICAL_FACTORS,
                label="Число точек измерения физических факторов (шум, ЭМИ)",
                type="number",
                required=True,
                integer=True,
            ),
            Field(
                name=VK_SAMPLING,
                label="Опробование окружающей среды",
                type="group",
                required=True,
                group_fields=[
                    Field(
                        name="sample_type",
                        label="Вид пробы",
                        type="text",
                        required=True,
                        dynamic_options=True,
                    ),
                    Field(
                        name="sample_count",
                        label="Количество, шт",
                        type="number",
                        required=True,
                        integer=True,
                    ),
                ],
            ),
            Field(
                name=VK_DATA_ACQUISITION,
                label="Получение исходных данных (в т.ч. платных), шт",
                type="number",
                required=True,
                integer=True,
            ),
            # Лабораторный блок (Э6г): вид анализа + количество, строки
            # сметы ecology.lab.*. Необязательная группа — пустая не
            # меняет смету (запасной путь — «Итого лабораторные» суммой).
            Field(
                name=VK_LAB_ANALYSES,
                label="Лабораторные анализы (этап III)",
                type="group",
                required=False,
                group_fields=[
                    Field(
                        name="analysis_type",
                        label="Вид анализа",
                        type="text",
                        required=True,
                        dynamic_options=True,
                    ),
                    Field(
                        name="analysis_count",
                        label="Количество (образцов / определений / проб)",
                        type="number",
                        required=True,
                        integer=True,
                    ),
                ],
            ),
            # Открытый вопрос (docs/known_risks.md): перенос полевого
            # коэффициента маршрутных наблюдений в камеральную строку не
            # выведен однозначно — поэтому отдельное поле оператора, а не
            # копия поля выше. По умолчанию 1 — нейтральный множитель
            # (консервативный вариант "без переноса").
            Field(
                name=VK_ROUTE_KAMERAL_COEF,
                label="Коэффициент к камеральной обработке маршрутных наблюдений",
                type="number",
                required=False,
                default=rule_default(VK_ROUTE_KAMERAL_COEF),
            ),
            Field(
                name=VK_LAB_TOTAL,
                label="Итого лабораторные работы (этап III), руб. в базисных ценах СБЦ-99 — "
                "только если анализы не введены строками на ведомости",
                type="number",
                required=False,
            ),
            # Вопрос В-38: в эталонных листах тяжёлые металлы в донных
            # отложениях без ×7 (1 — как в листе, по умолчанию), по смыслу
            # расценки — 7 элементов. Решает оператор.
            Field(
                name=VK_SEDIMENT_ELEMENTS,
                label="Тяжёлые металлы в донных отложениях — множитель на число элементов "
                "(1 — как в эталонных листах; 7 — по смыслу расценки: 7 элементов)",
                type="number",
                required=False,
                default=rule_default(VK_SEDIMENT_ELEMENTS),
                integer=True,
            ),
        ]

    def field_options(self, field_name: str, refs: Any) -> list[tuple[str, str]]:
        if field_name == "sample_type":
            # Фиксированный список видов проб — не из справочника (см.
            # SAMPLE_TYPE_CHOICES выше), поэтому refs не используется.
            return list(SAMPLE_TYPE_CHOICES)
        if field_name == "analysis_type":
            # Фиксированный список — строки лабораторного раздела эталонов.
            return list(LAB_ANALYSIS_CHOICES)
        if field_name == VK_PASSABILITY:
            return list(PASSABILITY_CHOICES)
        if field_name == VK_SUBBASE_ITEM:
            options = []
            for item in SUBBASE_ITEM_CHOICES:
                try:
                    price, _basis = refs.get_price(SOURCE, SUBBASE_TABLE, item, category=None)
                    price_text = f"{price:g} руб."
                except KeyError:
                    price_text = "цена не найдена в справочнике"
                options.append(
                    (item, f"Т80 п.{item} — {price_text} ({SUBBASE_ITEM_EXAMPLES[item]})")
                )
            return options
        return []

    def propose_volumes(self, tz: TzParams) -> Proposal:
        """Черновик ввода этапа ведомости по параметрам ТЗ (core/tz.py, 03.11.2026):
        протяжённость рекогносцировки и площадь радиационного обследования — оценки
        от протяжённости и площади съёмки; число точек, проб и анализов из ТЗ не
        выводится (в ТЗ — только перечень исследований)."""
        proposal = Proposal()
        if tz.object_kind == OBJECT_LINEAR and tz.length_km:
            proposal.set(
                VK_RECON_LENGTH, tz.length_km, KIND_ESTIMATE,
                "= протяжённость трассы из ТЗ (ДОПУЩЕНИЕ: обследование вдоль всей трассы)",
            )
        else:
            proposal.leave(VK_RECON_LENGTH, "протяжённость обследования — по протяжённости "
                                            "линейного объекта")
        area = tz.survey_area()
        if area is not None:
            proposal.set(
                VK_RADIATION, area.value, KIND_ESTIMATE,
                f"= площадь съёмки ({area.note}) — ПРЕДПОЛАГАЮ ту же полосу/участок, что у "
                "топографической съёмки; проверьте, требует ли ТЗ радиационное обследование",
            )
        else:
            proposal.leave(VK_RADIATION, "площадь — по полосе съёмки или площади участка (стр. 1)")
        for name, what in (
            (VK_RADON, "число точек измерения радона"),
            (VK_PHYSICAL_FACTORS, "число точек измерения шума и электромагнитных излучений"),
            (VK_SAMPLING, "виды и число проб"),
            (VK_DATA_ACQUISITION, "число запросов исходных данных"),
        ):
            proposal.leave(name, f"{what} — решение исполнителя (в ТЗ — только перечень работ)")
        return proposal

    def key_volume_fields(self) -> list[Field]:
        """Ключевые объёмы раздела для режима «ключевые объёмы» (core/tz.py)."""
        return key_volumes.key_volume_fields()

    def propose_from_key_volumes(self, tz: TzParams, keys: dict) -> Proposal:
        """Черновик ввода ведомости по ключевым объёмам: предложение по ТЗ +
        ключевые объёмы + достроенное по связям (modules/ecology/key_volumes.py)."""
        return key_volumes.propose(self, tz, keys)

    def build_volumes(self, data: dict, refs: Any) -> dict:
        sampling_rows = data.get(VK_SAMPLING) or []
        sample_counts: dict[str, float] = {}
        for row in sampling_rows:
            sample_type = row.get("sample_type")
            count = row.get("sample_count", 0) or 0
            key = f"{SAMPLING_VOLUME_KEY_PREFIX}{sample_type}"
            sample_counts[key] = sample_counts.get(key, 0) + count

        return {
            VK_FUND_MATERIALS: data.get(VK_FUND_MATERIALS, 0) or 0,
            VK_SUBBASE_ITEM: data.get(VK_SUBBASE_ITEM) or SUBBASE_ITEM_DEFAULT,
            VK_SUBBASE_VOLUME: data.get(VK_SUBBASE_VOLUME, 0) or 0,
            VK_PASSABILITY: data.get(VK_PASSABILITY) or PASSABILITY_DEFAULT,
            VK_RECON_LENGTH: data.get(VK_RECON_LENGTH, 0) or 0,
            VK_OBSERVATION_POINTS: data.get(VK_OBSERVATION_POINTS, 0) or 0,
            VK_ROUTE_FIELD_COEF: self._coefficient_or_neutral(data.get(VK_ROUTE_FIELD_COEF), VK_ROUTE_FIELD_COEF),
            VK_RADIATION: data.get(VK_RADIATION, 0),
            VK_RADON: data.get(VK_RADON, 0),
            VK_PHYSICAL_FACTORS: data.get(VK_PHYSICAL_FACTORS, 0),
            **sample_counts,
            VK_DATA_ACQUISITION: data.get(VK_DATA_ACQUISITION, 0),
            VK_ROUTE_KAMERAL_COEF: self._coefficient_or_neutral(data.get(VK_ROUTE_KAMERAL_COEF), VK_ROUTE_KAMERAL_COEF),
            VK_LAB_TOTAL: data.get(VK_LAB_TOTAL, 0) or 0,
            VK_SEDIMENT_ELEMENTS: self._coefficient_or_neutral(data.get(VK_SEDIMENT_ELEMENTS), VK_SEDIMENT_ELEMENTS),
            **_group_counts(data.get(VK_LAB_ANALYSES), "analysis_type", "analysis_count",
                            LAB_VOLUME_KEY_PREFIX),
        }

    @staticmethod
    def _coefficient_or_neutral(value: Any, name: str) -> float:
        # None — оператор поле не заполнял (вызов не из UI, где
        # подставляется Field.default): значение по умолчанию из справочника
        # правил параметров. Явно введённое значение, в т.ч. 0, не
        # подменяется — check() предупредит о нулевом.
        return rule_default(name) if value is None else float(value)

    def build_estimate(self, volumes: dict, refs: Any) -> dict[str, EstimateLine]:
        radiation_price, radiation_basis = get_price_or_missing(
            refs, SOURCE, RADIATION_TABLE, RADIATION_ITEM, FIELD_CATEGORY,
            "Радиационное обследование участка",
        )
        radiation = EstimateLine(
            id="ecology.radiation",
            name="Радиационное обследование участка",
            unit="га",
            qty=volumes[VK_RADIATION],
            price=radiation_price,
            coefs={},
            kind="A",
            base_ids=[],
            basis=radiation_basis,
        )

        radon_price, radon_basis = get_price_or_missing(
            refs, SOURCE, RADON_TABLE, RADON_ITEM, FIELD_CATEGORY,
            "Измерение плотности потока радона",
        )
        radon = EstimateLine(
            id="ecology.radon",
            name="Измерение плотности потока радона",
            unit="точек",
            qty=volumes[VK_RADON],
            price=radon_price,
            coefs={},
            kind="A",
            base_ids=[],
            basis=radon_basis,
        )

        physical_factors_name = "Измерение физических факторов (шум, ЭМИ)"
        physical_factors = EstimateLine(
            id="ecology.physical_factors",
            name=physical_factors_name,
            unit="точек",
            qty=volumes[VK_PHYSICAL_FACTORS],
            price=0.0,
            coefs={},
            kind="A",
            base_ids=[],
            basis=self._not_found_basis(physical_factors_name),
        )

        # --- Предполевые: программа работ (от ввода не зависит) ---
        program = self._priced_line(
            refs, "ecology.program", "Составление программы работ", "программа",
            volumes.get(VK_PROGRAM, 1),
            PROGRAM_TABLE, PROGRAM_ITEM, None, {"k_prim1": PROGRAM_COEFFICIENT},
        )

        # --- Полевые: рекогносцировочное обследование ---
        # Объём почвенной рекогносцировки и маршрутных наблюдений = объём
        # рекогносцировки: на обоих эталонах это ссылки на одну ячейку
        # (=D19 / =D21).
        codes = PASSABILITY_CODES[volumes[VK_PASSABILITY]]
        recon_km = volumes[VK_RECON_LENGTH]
        recon = self._priced_line(
            refs, "ecology.recon", "Инженерно-экологическая рекогносцировка", "км", recon_km,
            *codes["recon"], FIELD_CATEGORY, {"k_prim1": RECON_COEFFICIENT},
        )
        soil_recon = self._priced_line(
            refs, "ecology.soil_recon", "Почвенная рекогносцировка", "км",
            volumes.get(VK_SOIL_RECON_LENGTH, recon_km),
            *codes["soil_recon"], FIELD_CATEGORY, {},
        )
        route = self._priced_line(
            refs, "ecology.route",
            "Наблюдения при передвижении по маршруту (инженерно-экологическая карта)", "км",
            volumes.get(VK_ROUTE_LENGTH, recon_km), *codes["route"], FIELD_CATEGORY, {"k": volumes[VK_ROUTE_FIELD_COEF]},
        )
        observation_points = self._priced_line(
            refs, "ecology.observation_points",
            "Описание точек наблюдений (инженерно-экологическая карта)", "точек",
            volumes[VK_OBSERVATION_POINTS],
            OBSERVATION_POINTS_TABLE, OBSERVATION_POINTS_ITEM, FIELD_CATEGORY, {},
        )
        recon_field_lines = [recon, soil_recon, route, observation_points]

        fund_materials = self._priced_line(
            refs, "ecology.fund_materials",
            "Сбор, обработка и анализ опубликованных и фондовых материалов о состоянии "
            "природной среды", "10 цифр. знач.", volumes[VK_FUND_MATERIALS],
            FUND_MATERIALS_TABLE, FUND_MATERIALS_ITEM, None, {},
        )
        subbase = self._priced_line(
            refs, "ecology.subbase",
            "Подготовка электронной подосновы (схемы размещения объектов обустройства), привязка",
            "ед.", volumes[VK_SUBBASE_VOLUME], SUBBASE_TABLE, volumes[VK_SUBBASE_ITEM], None, {},
        )
        # "Разработка и составление запросов" (Автодорога, №4, "Т9 п.5",
        # 10% от предполевых) сознательно не добавлена: объём 0 на
        # эталоне, ссылка на Т9 п.5 ошибочна (см. ECOLOGY_ROAD_SKIP_ROWS).
        preliminary_lines = [program, fund_materials, subbase]

        lines: dict[str, EstimateLine] = {line.id: line for line in preliminary_lines}
        for line in recon_field_lines:
            lines[line.id] = line
        lines[radiation.id] = radiation
        lines[radon.id] = radon
        lines[physical_factors.id] = physical_factors

        sampling_line_ids: list[str] = []
        for sample_key, label in SAMPLE_TYPE_CHOICES:
            volume_key = f"{SAMPLING_VOLUME_KEY_PREFIX}{sample_key}"
            if volume_key not in volumes:
                continue  # оператор не вводил строку этого вида пробы

            line_id = f"ecology.sampling.{sample_key}"
            code = SAMPLE_TYPE_PRICE_CODES.get(sample_key)
            coefs: dict[str, float] = {}
            if code is None:
                price, basis = 0.0, self._not_found_basis(label)
            else:
                table, item, coefficient = code
                # Цена строки — цена справочника (та, что в основании),
                # коэффициент вида пробы — отдельным множителем в coefs
                # (как k_prim1 у программы работ), а не внутри цены.
                price, basis = get_price_or_missing(refs, SOURCE, table, item, None, label)
                if coefficient != 1.0:
                    coefs["k_sample"] = coefficient

            lines[line_id] = EstimateLine(
                id=line_id,
                name=label,
                unit="шт",
                qty=volumes[volume_key],
                price=price,
                coefs=coefs,
                kind="A",
                base_ids=[],
                basis=basis,
            )
            sampling_line_ids.append(line_id)

        data_acquisition_name = "Получение исходных данных (в т.ч. платных)"
        data_acquisition = EstimateLine(
            id="ecology.data_acquisition",
            name=data_acquisition_name,
            unit="шт",
            qty=volumes[VK_DATA_ACQUISITION],
            price=0.0,
            coefs={},
            kind="A",
            base_ids=[],
            basis=(
                f"{NOT_A_RATED_POSITION_PREFIX} для «{data_acquisition_name}» — в эталонной смете "
                "это фиксированные суммы за конкретные запросы (внешний прайс), а не расценка "
                "за единицу измерения (проверьте вручную)"
            ),
        )
        lines[data_acquisition.id] = data_acquisition

        # Пункт т.4 — по расстоянию до объекта (данные объекта в refs, Э5);
        # не задано — п.4 эталонов (основание прежнее).
        transport_choice = transport_item_by_distance(refs, SOURCE, TRANSPORT_TABLE, TRANSPORT_ITEM, module=self.code)
        transport_rate, transport_basis = get_price_or_missing(
            refs, SOURCE, TRANSPORT_TABLE, transport_choice.item, None,
            "Расходы по внутреннему транспорту",
        )
        if transport_choice.note:
            transport_basis = f"{transport_basis} — {transport_choice.note}"
        transport = EstimateLine(
            id="ecology.transport",
            name="Расходы по внутреннему транспорту",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": transport_rate},
            kind="B",
            base_ids=[
                *(line.id for line in recon_field_lines),
                radiation.id, radon.id, physical_factors.id, *sampling_line_ids,
            ],
            basis=transport_basis,
        )
        lines[transport.id] = transport

        # --- Лабораторные работы (этап III): строки анализов ---
        lab_line_ids: list[str] = []
        for analysis in LAB_ANALYSES:
            volume_key = f"{LAB_VOLUME_KEY_PREFIX}{analysis.key}"
            if volume_key not in volumes:
                continue  # оператор не вводил этот вид анализа
            if analysis.fixed_price is not None:
                price, basis = analysis.fixed_price, analysis.fixed_basis
            else:
                price, basis = get_price_or_missing(
                    refs, SOURCE, analysis.table, analysis.item, None, analysis.label
                )
            line_id = f"{LAB_LINE_PREFIX}{analysis.key}"
            coefs = dict(analysis.coefs)
            if analysis.key == "heavy_metals_sediments":
                # 1 (как в листе) — множитель не добавляется, строка прежняя.
                elements = volumes.get(VK_SEDIMENT_ELEMENTS, SEDIMENT_ELEMENTS_SHEET)
                if elements != SEDIMENT_ELEMENTS_SHEET:
                    coefs["k_elements"] = elements
            lines[line_id] = EstimateLine(
                id=line_id,
                name=analysis.label,
                unit=analysis.unit,
                qty=volumes[volume_key],
                price=price,
                coefs=coefs,
                kind="A",
                base_ids=[],
                basis=basis,
                rounding=analysis.rounding,
            )
            lab_line_ids.append(line_id)

        # --- Камеральные: пары полевых строк ---
        # ДОПУЩЕНИЕ нового шаблона сметы (эталоны Карьер/Автодорога):
        # объём камеральной строки = объём её полевой строки. На старых
        # версиях шаблона компании встречались расхождения (вписанные вручную
        # объёмы) — см. docs/known_risks.md.
        def kameral_qty(pair: str, field_qty: float) -> float:
            return volumes.get(f"{KAMERAL_VOLUME_KEY_PREFIX}{pair}", field_qty)

        kameral_lines = [
            self._priced_line(
                refs, "ecology.kameral.recon",
                "Камеральная обработка инженерно-экологической рекогносцировки", "км",
                kameral_qty("recon", recon.qty), *codes["recon"], KAMERAL_CATEGORY,
                {"k_prim1": RECON_COEFFICIENT},  # фиксированный, не копия полевого
            ),
            self._priced_line(
                refs, "ecology.kameral.soil_recon",
                "Камеральная обработка почвенной рекогносцировки", "км",
                kameral_qty("soil_recon", soil_recon.qty), *codes["soil_recon"],
                KAMERAL_CATEGORY, {},
            ),
            self._priced_line(
                refs, "ecology.kameral.route",
                "Камеральная обработка наблюдений для составления инженерно-экологической карты",
                "км", kameral_qty("route", route.qty), *codes["route"], KAMERAL_CATEGORY,
                {"k": volumes[VK_ROUTE_KAMERAL_COEF]},  # поле оператора, открытый вопрос
            ),
            self._priced_line(
                refs, "ecology.kameral.observation_points",
                "Камеральная обработка точек наблюдений", "точек",
                kameral_qty("observation_points", observation_points.qty),
                OBSERVATION_POINTS_TABLE, OBSERVATION_POINTS_ITEM,
                KAMERAL_CATEGORY, {},
            ),
            self._priced_line(
                refs, "ecology.kameral.radiation",
                "Камеральная обработка радиационного обследования участка", "га",
                kameral_qty("radiation", radiation.qty), RADIATION_TABLE, RADIATION_ITEM, KAMERAL_CATEGORY,
                dict(radiation.coefs),  # коэффициент полевой строки переносится (2 из 2 примеров)
            ),
            self._priced_line(
                refs, "ecology.kameral.radon",
                "Камеральная обработка измерений плотности потока радона", "точек",
                kameral_qty("radon", radon.qty), RADON_TABLE, RADON_ITEM, KAMERAL_CATEGORY, {},
            ),
            self._kameral_lab_line(refs, lab_line_ids, volumes.get(VK_LAB_TOTAL, 0) or 0),
        ]
        for line in kameral_lines:
            lines[line.id] = line

        # --- Технический отчёт: 0,21 × (предполевые + камеральные) ---
        # Предполевые = программа работ + фондовые материалы + подоснова
        # (как "Итого по п.п. 1-3/1-4" эталонов).
        report_rate, report_basis = get_price_or_missing(
            refs, SOURCE, REPORT_TABLE, REPORT_ITEM, None, "Составление технического отчёта"
        )
        report = EstimateLine(
            id="ecology.report",
            name="Составление технического отчёта",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": report_rate},
            kind="B",
            base_ids=[
                *(line.id for line in preliminary_lines),
                *(line.id for line in kameral_lines),
            ],
            basis=report_basis,
        )
        lines[report.id] = report

        return lines

    # --- многоэтапный сценарий (core/stages.py) ---

    def build_volume_sheet(
        self, data: dict, pinned: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, VolumeLine]:
        """Ведомость объёмов (этап 1): что и сколько делать, без цен и
        без параметров расценки. Прямой ввод — объёмы из формы; по
        жёсткой связи генерируются программа работ (1), почвенная
        рекогносцировка и маршрутные наблюдения (= км рекогносцировки,
        на эталонах — ссылки на одну ячейку), шесть камеральных пар
        (= объём своей полевой строки) и технический отчёт (1).
        Зависимая строка следует за исправленной строкой-источником,
        собственная правка зависимой побеждает (решение №6)."""
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

        def given(key, name, unit, qty):
            return put(key, name, unit, qty, SOURCE_INPUT, "ввод оператора")

        # Предполевые
        auto(VK_PROGRAM, "Составление программы работ", "программа", 1, "одна на объект")
        given(
            VK_FUND_MATERIALS,
            "Сбор, обработка и анализ опубликованных и фондовых материалов о состоянии "
            "природной среды", "10 цифр. знач.", data.get(VK_FUND_MATERIALS, 0) or 0,
        )
        given(
            VK_SUBBASE_VOLUME,
            "Подготовка электронной подосновы (схемы размещения объектов обустройства), привязка",
            "ед.", data.get(VK_SUBBASE_VOLUME, 0) or 0,
        )

        # Полевые
        recon_km = given(
            VK_RECON_LENGTH, "Инженерно-экологическая рекогносцировка", "км",
            data.get(VK_RECON_LENGTH, 0) or 0,
        )
        field_qty = {VK_RECON_LENGTH: recon_km}
        field_qty[VK_SOIL_RECON_LENGTH] = auto(
            VK_SOIL_RECON_LENGTH, "Почвенная рекогносцировка", "км", recon_km,
            "= протяжённость рекогносцировки",
        )
        field_qty[VK_ROUTE_LENGTH] = auto(
            VK_ROUTE_LENGTH,
            "Наблюдения при передвижении по маршруту (инженерно-экологическая карта)", "км",
            recon_km, "= протяжённость рекогносцировки",
        )
        field_qty[VK_OBSERVATION_POINTS] = given(
            VK_OBSERVATION_POINTS, "Описание точек наблюдений (инженерно-экологическая карта)",
            "точек", data.get(VK_OBSERVATION_POINTS, 0) or 0,
        )
        field_qty[VK_RADIATION] = given(
            VK_RADIATION, "Радиационное обследование участка", "га", data.get(VK_RADIATION, 0),
        )
        field_qty[VK_RADON] = given(
            VK_RADON, "Измерение плотности потока радона", "точек", data.get(VK_RADON, 0),
        )
        given(
            VK_PHYSICAL_FACTORS, "Измерение физических факторов (шум, ЭМИ)", "точек",
            data.get(VK_PHYSICAL_FACTORS, 0),
        )

        sample_counts: dict[str, float] = {}
        for row in data.get(VK_SAMPLING) or []:
            key = f"{SAMPLING_VOLUME_KEY_PREFIX}{row.get('sample_type')}"
            sample_counts[key] = sample_counts.get(key, 0) + (row.get("sample_count", 0) or 0)
        # порядок — как в смете (по SAMPLE_TYPE_CHOICES)
        for sample_key, label in SAMPLE_TYPE_CHOICES:
            key = f"{SAMPLING_VOLUME_KEY_PREFIX}{sample_key}"
            if key in sample_counts:
                given(key, label, "шт", sample_counts[key])

        given(
            VK_DATA_ACQUISITION, "Получение исходных данных (в т.ч. платных)", "шт",
            data.get(VK_DATA_ACQUISITION, 0),
        )

        # Лабораторные анализы (этап III) — ввод, порядок — как в смете
        lab_counts = _group_counts(data.get(VK_LAB_ANALYSES), "analysis_type",
                                   "analysis_count", LAB_VOLUME_KEY_PREFIX)
        for analysis in LAB_ANALYSES:
            key = f"{LAB_VOLUME_KEY_PREFIX}{analysis.key}"
            if key in lab_counts:
                given(key, analysis.label, analysis.unit, lab_counts[key])

        # Камеральные пары — объём = объём полевой строки
        kameral_names = {
            "recon": ("Камеральная обработка инженерно-экологической рекогносцировки", "км"),
            "soil_recon": ("Камеральная обработка почвенной рекогносцировки", "км"),
            "route": (
                "Камеральная обработка наблюдений для составления инженерно-экологической карты",
                "км",
            ),
            "observation_points": ("Камеральная обработка точек наблюдений", "точек"),
            "radiation": ("Камеральная обработка радиационного обследования участка", "га"),
            "radon": ("Камеральная обработка измерений плотности потока радона", "точек"),
        }
        for pair, field_key in KAMERAL_PAIRS.items():
            name, unit = kameral_names[pair]
            auto(
                f"{KAMERAL_VOLUME_KEY_PREFIX}{pair}", name, unit, field_qty[field_key],
                f"= объём полевой строки «{lines[field_key].name}»",
            )

        auto(VK_REPORT, "Составление технического отчёта", "отчёт", 1, "один на объект")
        return lines

    def stages(self) -> list[Stage]:
        """Ведомость объёмов → смета. Параметры расценки
        (ESTIMATE_PARAMETER_FIELDS) — поля этапа сметы."""
        schema = self.input_schema()
        volume_fields = [f for f in schema if f.name not in ESTIMATE_PARAMETER_FIELDS]
        estimate_fields = [f for f in schema if f.name in ESTIMATE_PARAMETER_FIELDS]

        def compute_volumes(inputs, previous, refs, pinned):
            return self.build_volume_sheet(inputs, pinned)

        def compute_estimate(inputs, previous, refs, pinned):
            # Параметры — та же обработка значений по умолчанию, что в
            # build_volumes (пустое → значение по умолчанию/нейтральный
            # коэффициент), объёмы — из утверждённой ведомости.
            parameters = self.build_volumes(inputs, refs=refs)
            volumes = {
                **previous["volumes"].values(),
                **{name: parameters[name] for name in ESTIMATE_PARAMETER_FIELDS},
            }
            # Начисления (С3) — по ставкам данных объекта; не заданы — как раньше.
            return apply_accruals(self.build_estimate(volumes, refs=refs), ACCRUALS, refs)

        # См. core/module_helpers.py: ввод — в форме, объём с ведомостью
        # на смете не правится (решение №5), у долей — ставка.
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
                editable_fields=volumes_editable,
                confirm_label="Подтвердить ведомость",
                context_warnings=_volume_zero_warnings,
            ),
            Stage(
                number=2,
                key="estimate",
                title="Смета",
                fields=estimate_fields,
                compute=compute_estimate,
                # Все предупреждения модуля — о ценах, коэффициентах,
                # пункте Т80 и итоге лабораторных, то есть о смете.
                check=self.check,
                editable_fields=estimate_editable,
                context_warnings=estimate_context_warnings,
                confirm_label="Подтвердить смету",
            ),
        ]

    def _kameral_lab_line(
        self, refs: Any, lab_line_ids: list[str], lab_total: float
    ) -> EstimateLine:
        """0,2 × ИТОГО лабораторных (Т86 п.6).

        Есть строки анализов — «доля от базы» (kind B) от них, как
        №2.5_ИЭИ!H79 = 0,2 × H70. Строк нет — прежний запасной путь: объём
        строки = итог лабораторных, введённый суммой, цена = доля 0,2
        (kind A, ссылаться не на что). Введены оба — по строкам, сумма не
        используется (отметка в основании, check() предупреждает)."""
        name = "Камеральная обработка лабораторных работ"
        if not lab_line_ids:
            return self._priced_line(
                refs, "ecology.kameral.lab", name, "руб.", lab_total,
                LAB_KAMERAL_TABLE, LAB_KAMERAL_ITEM, None, {},
            )
        rate, basis = get_price_or_missing(
            refs, SOURCE, LAB_KAMERAL_TABLE, LAB_KAMERAL_ITEM, None, name
        )
        basis = f"{basis} — доля от суммы строк лабораторных анализов"
        if lab_total:
            basis = (f"{basis}; {LAB_TOTAL_IGNORED_MARK} ({lab_total:g} руб.) — "
                     "посчитано по строкам анализов")
        return EstimateLine(
            id="ecology.kameral.lab",
            name=name,
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": rate},
            kind="B",
            base_ids=list(lab_line_ids),
            basis=basis,
        )

    def _priced_line(
        self, refs: Any, line_id: str, name: str, unit: str, qty: float,
        table: str, item: str, category: str | None, coefs: dict[str, float],
    ) -> EstimateLine:
        price, basis = get_price_or_missing(refs, SOURCE, table, item, category, name)
        return EstimateLine(
            id=line_id,
            name=name,
            unit=unit,
            qty=qty,
            price=price,
            coefs=coefs,
            kind="A",
            base_ids=[],
            basis=basis,
        )

    @staticmethod
    def _not_found_basis(name: str) -> str:
        return (
            f"{NOT_A_RATED_POSITION_PREFIX} для «{name}» — ни в одной найденной смете заказчика "
            "нет кода СБЦ-99 под эту позицию (проверьте вручную)"
        )

    def check(self, estimate: dict[str, EstimateLine]) -> list[str]:
        warnings = []

        for line in estimate.values():
            if line.basis.startswith(MISSING_PRICE_BASIS_PREFIX):
                warnings.append(
                    f"Цена для «{line.name}» не найдена в справочнике, требуется ручная проверка"
                )
            elif line.basis.startswith(NOT_A_RATED_POSITION_PREFIX):
                warnings.append(
                    f"«{line.name}»: не найдена как расценка СБЦ-99 ни в одной смете заказчика "
                    "— требуется отдельный источник цены"
                )

        kameral_route = estimate.get("ecology.kameral.route")
        if kameral_route is not None and kameral_route.qty:
            coefficient = kameral_route.coefs.get("k", NEUTRAL_COEFFICIENT)
            warnings.append(
                f"Коэффициент камеральной обработки маршрутных наблюдений задан оператором "
                f"({coefficient:g}) — правило переноса из полевой строки не выведено однозначно, "
                "проверьте"
            )
            if coefficient <= 0:
                warnings.append(
                    "Коэффициент камеральной обработки маршрутных наблюдений ≤ 0 — строка обнулена"
                )

        kameral_lab = estimate.get("ecology.kameral.lab")
        has_sampling = any(line_id.startswith("ecology.sampling.") for line_id in estimate)
        lab_by_rows = kameral_lab is not None and kameral_lab.kind == "B"
        if kameral_lab is not None and not lab_by_rows and not kameral_lab.qty and has_sampling:
            warnings.append(
                "Опробование введено, а лабораторные анализы — нет (ни строками на ведомости, "
                "ни суммой «Итого лабораторные работы»): камеральная обработка лабораторных "
                "(0,2 × итого, Т86 п.6) посчитана от 0"
            )
        if lab_by_rows and LAB_TOTAL_IGNORED_MARK in kameral_lab.basis:
            warnings.append(
                "Лабораторные введены дважды — строками анализов и суммой «Итого лабораторные "
                "работы»: смета посчитана по строкам анализов, сумма не использована. Очистите "
                "поле суммы или удалите строки анализов."
            )

        sediments = estimate.get(f"{LAB_LINE_PREFIX}heavy_metals_sediments")
        if sediments is not None and sediments.qty:
            elements = sediments.coefs.get("k_elements", SEDIMENT_ELEMENTS_SHEET)
            if elements == SEDIMENT_ELEMENTS_SHEET:
                warnings.append(
                    "Тяжёлые металлы в донных отложениях посчитаны как в эталонных листах — "
                    "без ×7: в эталонных листах ×7 не применён (вероятно, ошибка шаблона); по "
                    "смыслу расценки (т.70 §62 — цена за один элемент) — 7 элементов; уточните. "
                    "Множитель задаётся параметром сметы."
                )
            else:
                warnings.append(
                    f"Тяжёлые металлы в донных отложениях: множитель числа элементов {elements:g} "
                    "задан оператором — в эталонных листах ×7 не применён (без множителя), "
                    "смета отличается от листа по этой строке."
                )

        if lab_by_rows:
            # Без строк анализов вообще — общее предупреждение ниже, поштучные
            # не дублируются.
            prefix_s, prefix_l = "ecology.sampling.", LAB_LINE_PREFIX
            warnings.extend(lab_link_warnings(
                {i[len(prefix_s):]: line.qty for i, line in estimate.items() if i.startswith(prefix_s)},
                {i[len(prefix_l):]: line.qty for i, line in estimate.items() if i.startswith(prefix_l)},
            ))

        subbase = estimate.get("ecology.subbase")
        if subbase is not None and subbase.qty:
            warnings.append(
                f"Пункт Т80 для подготовки электронной подосновы выбран оператором "
                f"({subbase.basis}) — основание выбора п.13/п.14 не выведено, проверьте"
            )

        if not lab_by_rows:
            warnings.append(
                "Упрощение модуля относительно эталонной сметы: лабораторные анализы не введены "
                "строками — ИТОГО лабораторных работ взят из суммы, введённой оператором, "
                "стоимости лабораторных в смете нет и в базу районного коэффициента они не "
                "входят. Введите анализы на ведомости (группа «Лабораторные анализы»)."
            )

        return warnings
