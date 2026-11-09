"""Модуль геофизики (ИГФИ). Сборник — СЦиР-1982, эталон — лист
"№1.2.2_ИГФИ" (reference_data/Автодорога_эталон.xlsx,
итог K34 = 4 417 072,88 руб. = база K33 53 899,61 × 81,95).

Позиции листа (Э6в, 30.10.2026 — модуль доведён до итога листа):

- полевые: ВЭЗ (т.267 п.3; строка 17), сейсморазведка МПВ (т.258 п.83) — на
  листе ДВЕ строки, продольные и поперечные волны (18, 19). Тип волн — ввод
  ведомости: «продольные» (по умолчанию, как до Э6в — одна строка), «поперечные»
  или «оба типа» (объём МПВ и камералки МПВ удваиваются — по строке на тип);
- камеральные: программа работ (т.294 п.3, строка 22), камеральная обработка
  данных электроразведки — 30 % стоимости ВЭЗ (стр.288, строка 23), камеральная
  обработка МПВ (т.291 п.1, строки 24–25; объём = объём МПВ своего типа волн),
  технический отчёт (т.294 п.10, строка 26: 1000 + 10 % камеральных);
- внутренний транспорт 5 % от полевых (т.4 п.1, строка 28);
- начисления уровня сметы (строки 29–32) — `ACCRUALS` и core/accruals.py;
  организация и ликвидация (K31) — на выбор: ставка из данных объекта (как
  у других разделов, по умолчанию) или формула листа (см. `_org_liquidation_lines`).

Коэффициенты листа, кроме К=1,2 к ВЭЗ (G17, применялся с самого начала), —
параметры этапа сметы со значением по умолчанию 1 (нейтрально: контрольная
цифра сквозного прогона не меняется). В подписи поля — значение и ячейка
листа. Основание по бумажному СЦиР-1982 подтвердить нельзя (сборника в
репозитории нет) — только «по листу эталона, ячейка …».

Почему один и тот же «К=1.2 с.252 п.6» к ВЭЗ применяется всегда, а к МПВ —
нет (находка ревью, 30.10.2026). В листе он стоит у обеих позиций (C17/G17 у
ВЭЗ, C18–C19/H18–H19 у МПВ). Различие историческое, а не по смыслу работ: к
ВЭЗ коэффициент применялся с первой версии модуля и вошёл в контрольную
цифру сквозного прогона (28 680 880,97), поэтому остался константой
`VEZ_COEFFICIENT`; у МПВ его не было, и при доводке до листа (Э6в) он стал
параметром сметы со значением 1 — чтобы контрольная цифра не менялась.
Условие применения в листе не указано, по бумажному сборнику не сверено.
Привести к одному правилу (оба — всегда или оба — параметр) решает
пользователь (список вопросов В-37); пока — предупреждение в `check()`.

Оргликвидация по формуле листа (ревью, 30.10.2026): при нулевых полевых
работах (все объёмы ВЭЗ и МПВ = 0) не начисляется — и доля, и постоянная
часть 5 500 × К; пустой К — 1 с предупреждением (в листе 1,2); ставки
оргликвидации из данных объекта в этом режиме не используются —
предупреждение. Коэффициент 0 или меньше — предупреждение этапа сметы
(обнуляет или делает отрицательной строку).

Новые позиции (программа, обработка ЭР, техотчёт) по умолчанию не
создаются — их включает оператор в ведомости.

Камералка МПВ — kind="A" с собственными qty/price (на листе у неё есть свои
объём и цена, а не процент от МПВ). Внутренний транспорт — ДРУГАЯ ставка
(5 %, т.4 п.1), чем у геологии (8,75 %) — обе из справочника."""

from dataclasses import replace
from typing import Any

from core import param_rules
from core.accruals import (
    EXTERNAL_TRANSPORT,
    METROLOGY,
    ORG_LIQUIDATION,
    REGIONAL_FIELD,
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
from core.object_data import object_data_of
from core.stages import SOURCE_CLASS_A, SOURCE_INPUT, SOURCE_OPERATOR, Stage, VolumeLine
from core.tz import KIND_RULE, Proposal, TzParams
from modules.geophysics import key_volumes

SOURCE = "СЦиР-1982"
SHEET = "№1.2.2_ИГФИ"

VEZ_TABLE = "267"
VEZ_ITEM = "3"
MPV_TABLE = "258"
MPV_ITEM = "83"
KAMERALKA_MPV_TABLE = "291"
KAMERALKA_MPV_ITEM = "1"
TRANSPORT_TABLE = "4"
TRANSPORT_ITEM = "1"
PROGRAM_TABLE = "294"
PROGRAM_ITEM = "3"
REPORT_TABLE = "294"
REPORT_ITEM = "10"

# К=1.2 с.252 п.6 к ВЭЗ — лист G17; применяется с первой версии модуля
# (входит в контрольную цифру сквозного прогона), поэтому остаётся
# константой, а не параметром.
VEZ_COEFFICIENT = 1.2

# Камеральная обработка данных электроразведки: 30 % стоимости ВЭЗ (строки
# с коэффициентами) — лист F23 (основание C23 «стр.288», без таблицы и пункта,
# поэтому парсер справочника строку не берёт — константа с источником).
OFFICE_VEZ_SHARE = 0.3
# Технический отчёт: постоянная часть 1000 руб. — лист E26 (формула K26 =
# E26 + 10 % × SUM(K22:K25)); 10 % — из справочника (т.294 п.10, F26).
# Коэффициент H26 = 1,2 («К=1.2 т.294, прим.3») формулой K26 НЕ применяется —
# программа считает, как формула листа.
REPORT_FIXED = 1000.0
# Организация и ликвидация по формуле листа (K31):
# ((F × 3 % + 3000) + (F × 0,025 % + 2500)) × G31, F = полевые + вн. транспорт.
ORG_SHEET_PERCENT = 0.03 + 0.00025
ORG_SHEET_FIXED = 3000.0 + 2500.0

# --- ведомость (этап 1) ---
VK_VEZ = "vez"
VK_MPV = "mpv"
# Поперечные волны при «оба типа» (лист, строки 19 и 25).
VK_MPV_S = "mpv_s"
# Камералка МПВ — та же единица («ф.н.»), что и поле, и на листе объём
# буквально равен объёму поля (400 = 400) — жёсткая связь, не оценка.
VK_KAMERALKA_MPV = "kameralka_mpv"
VK_KAMERALKA_MPV_S = "kameralka_mpv_s"
VK_PROGRAM = "program"
VK_OFFICE_VEZ = "office_vez"
VK_REPORT = "report"

F_MPV_WAVES = "mpv_waves"
F_PROGRAM = "program_count"
F_OFFICE_VEZ = "office_vez"
F_REPORT = "report"

# Значение выбора по умолчанию — пустая строка: интерфейс не считает её
# «введёнными данными» (app/ui.py::_meaningful) и не просит подтверждать
# замену формы при открытии сметы; пусто = как до Э6в.
DEFAULT_CHOICE = ""
WAVES_P = "P"
WAVES_S = "S"
WAVES_BOTH = "PS"
WAVES_OPTIONS = [
    (DEFAULT_CHOICE, "продольные волны (одна строка МПВ, как раньше)"),
    (WAVES_S, "поперечные волны (одна строка МПВ)"),
    (WAVES_BOTH, "оба типа — продольные и поперечные (объём МПВ и камералки × 2, как в эталоне)"),
]
WAVE_NAMES = {WAVES_P: "продольные волны", WAVES_S: "поперечные волны"}

YES = "yes"
YES_NO_OPTIONS = [(DEFAULT_CHOICE, "нет"), (YES, "да (в эталоне — да)")]

# --- параметры сметы (этап 2): коэффициенты листа, по умолчанию 1 ---
P_K_VEZ_265_2 = "k_vez_265_2"
P_K_VEZ_265_9 = "k_vez_265_9"
P_K_MPV_252_6 = "k_mpv_252_6"
P_K_OFFICE_P22 = "k_office_p22"
P_K_OFFICE_MPV_NOTE2 = "k_office_mpv_note2"
P_ORG_MODE = "org_liquidation_mode"
P_ORG_SHEET_K = "org_liquidation_sheet_k"

ORG_MODE_SHEET = "sheet"
ORG_MODE_OPTIONS = [
    (DEFAULT_CHOICE,"ставка × коэффициент из данных объекта (как у других разделов)"),
    (ORG_MODE_SHEET, "по формуле эталона: ((F×3 % + 3 000) + (F×0,025 % + 2 500)) × К, "
                     "F = полевые + внутренний транспорт (лист №1.2.2_ИГФИ, ячейка K31)"),
]

# Ключ coefs строки → (параметр, подпись основания). В coefs коэффициент
# попадает только если он не равен 1 (по умолчанию строка — как до Э6в).
COEF_PARAMS = {
    P_K_VEZ_265_2: ("k_265_2", "К=1.15 т.265 п.2"),
    P_K_VEZ_265_9: ("k_265_9", "К=1.1 т.265 п.9"),
    P_K_MPV_252_6: ("k_252_6", "К=1.2 с.252 п.6"),
    P_K_OFFICE_P22: ("k_p22", "К=1.15 П-22"),
    P_K_OFFICE_MPV_NOTE2: ("k_prim2", "К=1.15 прим.2"),
}

FIELD_IDS = ("geophysics.vez", "geophysics.mpv", "geophysics.mpv_s")
OFFICE_IDS = (
    "geophysics.program", "geophysics.office_vez", "geophysics.kameralka_mpv",
    "geophysics.kameralka_mpv_s", "geophysics.report", "geophysics.report_fixed",
)
ORG_FIXED_ID = "geophysics.accrual.org_liquidation_fixed"

# Начисления уровня сметы (С3, core/accruals.py) — по формулам листа
# «№1.2.2_ИГФИ», строки 29–32 (без округления — как в листе):
#   K29 внешний транспорт = (полевые + вн.тр.) × ставка (в эталоне 6,5 %);
#   K30 метрология = 5 % × (полевые + камеральные + вн.тр. + внеш.тр.) — «К=0.05 П-22»;
#   K31 оргликвидация — ставка × коэффициент от (полевые + вн.тр.) либо, по выбору
#       оператора в смете, формула листа (`_org_liquidation_lines`);
#   K32 районный к полевым = (полевые + вн.тр. + внеш.тр. + метрология + оргликв.) × ставка;
#   к камеральным районный в эталоне не начисляется.
ACCRUALS = AccrualScheme(
    module="geophysics",
    groups={
        "field": lambda i: i in FIELD_IDS,
        "office": lambda i: i in OFFICE_IDS,
        "internal_transport": lambda i: i == "geophysics.transport",
    },
    bases={
        EXTERNAL_TRANSPORT: ("field", "internal_transport"),
        ORG_LIQUIDATION: ("field", "internal_transport"),
        METROLOGY: ("field", "office", "internal_transport", EXTERNAL_TRANSPORT),
        REGIONAL_FIELD: ("field", "internal_transport", EXTERNAL_TRANSPORT, METROLOGY,
                         ORG_LIQUIDATION),
    },
    fixed_rates={METROLOGY: 0.05},
    applied_warnings={
        ORG_LIQUIDATION: "Организация и ликвидация работ посчитана как «ставка × коэффициент» от "
        "(полевые + внутренний транспорт); в эталоне геофизики (лист №1.2.2_ИГФИ, K31) — "
        "формула с фиксированными суммами, результат отличается в разы. Её можно выбрать в "
        "параметрах сметы («Организация и ликвидация работ — способ расчёта»).",
    },
    sources={
        EXTERNAL_TRANSPORT: "СЦиР-1982 т.5 (эталон №1.2.2_ИГФИ!K29)",
        ORG_LIQUIDATION: "ОУ п.13 (в эталоне №1.2.2_ИГФИ!K31 — иная формула)",
        METROLOGY: "К=0.05 П-22 (эталон №1.2.2_ИГФИ!K30)",
        REGIONAL_FIELD: "т.3 п.3, п.7е ОУ П-22 (эталон №1.2.2_ИГФИ!K32)",
    },
)
# Схема без оргликвидации — для режима «по формуле листа»: строки
# оргликвидации модуль строит сам и добавляет в базу районного.
ACCRUALS_WITHOUT_ORG = replace(
    ACCRUALS,
    bases={name: tuple(p for p in parts if p != ORG_LIQUIDATION)
           for name, parts in ACCRUALS.bases.items() if name != ORG_LIQUIDATION},
)


# Находка ревью №10 (30.10.2026): объёмы, которые по смыслу работ и эталонам
# должны быть > 0, когда раздел не пустой, — предупреждение этапа ведомости
# (core/module_helpers.py::zero_volume_warnings; список — список вопросов В-8).
VOLUME_EXPECTED_NONZERO = {"vez_count": "Объём ВЭЗ, физ.точек", "mpv_count": "Объём сейсморазведки МПВ, физ.наблюдений"}
VOLUME_EXPECTED_NONZERO_IN_ROWS = {}


def _volume_zero_warnings(inputs, previous, refs):
    return zero_volume_warnings(inputs, VOLUME_EXPECTED_NONZERO, VOLUME_EXPECTED_NONZERO_IN_ROWS)


def _number(value: Any, default: float) -> float:
    """Число из ввода; пусто (None, "", нет ключа — старые сметы) — default."""
    if value is None or value == "":
        return default
    return float(value)


def coef_default(name: str) -> float:
    """Коэффициент листа по умолчанию — справочник правил параметров
    (core/param_rules.py; сейчас 1 — не применяется)."""
    return param_rules.default("geophysics", name, 1.0)


def org_mode(params: dict) -> str:
    """Способ расчёта оргликвидации; не выбран — по справочнику правил (сейчас
    «ставка × коэффициент», пустое значение)."""
    value = (params or {}).get(P_ORG_MODE)
    if value is None or value == "":
        value = param_rules.default("geophysics", P_ORG_MODE, "")
    return value or ""


def _waves(data: dict) -> str:
    value = data.get(F_MPV_WAVES)
    return value if value in (WAVES_P, WAVES_S, WAVES_BOTH) else WAVES_P


def _estimate_line_volume_key(line_id: str) -> str:
    key = line_id.removeprefix("geophysics.")
    return VK_REPORT if key == "report_fixed" else key


COEF_TITLES = {
    P_K_VEZ_265_2: "ВЭЗ: коэффициент «т.265 п.2»",
    P_K_VEZ_265_9: "ВЭЗ: коэффициент «т.265 п.9»",
    P_K_MPV_252_6: "МПВ: коэффициент «с.252 п.6»",
    P_K_OFFICE_P22: "Камеральные работы: коэффициент «П-22»",
    P_K_OFFICE_MPV_NOTE2: "Камеральная обработка МПВ: коэффициент «прим.2»",
    P_ORG_SHEET_K: "Организация и ликвидация по формуле эталона: коэффициент К",
}


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _coefficient_warnings(inputs: dict) -> list[str]:
    """Коэффициент 0 или меньше (ревью, 30.10.2026): расчёт его принимает,
    но строка обнуляется (или становится отрицательной) — почти наверняка
    ошибка ввода, предупреждаем."""
    warnings = []
    for name, title in COEF_TITLES.items():
        value = inputs.get(name)
        if _blank(value):
            continue
        if name == P_ORG_SHEET_K and org_mode(inputs) != ORG_MODE_SHEET:
            continue  # поле действует только при расчёте «по формуле эталона»
        if float(value) <= 0:
            warnings.append(
                f"{title} = {float(value):g}: коэффициент 0 или меньше обнуляет строку сметы (или делает "
                "её отрицательной). Если коэффициент не применяется — оставьте 1."
            )
    return warnings


def _org_sheet_warnings(inputs: dict, previous: Any, refs: Any) -> list[str]:
    """Предупреждения режима «оргликвидация по формуле эталона»."""
    warnings = []
    volumes = previous.get("volumes")
    if volumes is not None and not any(
        (line.qty or 0) > 0 for key, line in volumes.lines.items() if key in (VK_VEZ, VK_MPV, VK_MPV_S)
    ):
        warnings.append(
            "Организация и ликвидация по формуле эталона не начислена: полевых работ нет (объёмы ВЭЗ "
            "и МПВ равны 0), база F формулы листа (K31) — ноль."
        )
    if _blank(inputs.get(P_ORG_SHEET_K)):
        warnings.append(
            "Организация и ликвидация по формуле эталона: коэффициент К не задан — применён 1; в "
            f"эталоне (лист {SHEET}, ячейка G31) — 1,2 для районов, приравненных к Крайнему Северу."
        )
    data = object_data_of(refs)
    if (data.rate(GeophysicsModule.code, "org_liquidation_rate") is not None
            or data.rate(GeophysicsModule.code, "org_liquidation_k") is not None):
        warnings.append(
            "В данных объекта заданы ставка или коэффициент организации и ликвидации, но для геофизики "
            "выбран расчёт по формуле эталона — ставки объекта для этого раздела не используются."
        )
    return warnings


class GeophysicsModule:
    code = "geophysics"

    # --- поля ввода ---

    def volume_schema(self) -> list[Field]:
        return [
            Field(name="vez_count", label="Объём ВЭЗ, физ.точек", type="number",
                  required=True, integer=True),
            Field(name="mpv_count", label="Объём сейсморазведки МПВ, физ.наблюдений (на один тип волн)",
                  type="number", required=True, integer=True),
            Field(name=F_MPV_WAVES,
                  label="Сейсморазведка МПВ: тип волн (в эталоне №1.2.2_ИГФИ — оба типа, строки 18–19)",
                  type="text", required=False, dynamic_options=True),
            Field(name=F_PROGRAM,
                  label="Составление программы работ (т.294 п.3), шт. (в эталоне — 1; 0 — строки нет)",
                  type="number", required=False, integer=True),
            Field(name=F_OFFICE_VEZ,
                  label="Камеральная обработка данных электроразведки (ВЭЗ) — 30 % стоимости ВЭЗ (стр.288)",
                  type="text", required=False, dynamic_options=True),
            Field(name=F_REPORT,
                  label="Составление технического отчёта (т.294 п.10: 1000 руб. + 10 % камеральных)",
                  type="text", required=False, dynamic_options=True),
        ]

    def estimate_schema(self) -> list[Field]:
        """Параметры этапа сметы: коэффициенты листа (по умолчанию 1 — не
        применяются) и способ расчёта оргликвидации."""

        def coef(name: str, label: str) -> Field:
            return Field(name=name, label=label, type="number", required=False,
                         default=coef_default(name))

        # Подписи (ревью, 30.10.2026): к чему применяется и при каком условии.
        # Условия берутся только из подписей листа; где лист их не называет —
        # так и написано (бумажного СЦиР-1982 в репозитории нет).
        return [
            coef(P_K_VEZ_265_2,
                 "ВЭЗ: коэффициент «т.265 п.2» — умножает стоимость ВЭЗ. В листе эталона 1,15 "
                 "(ячейка I17); при каком условии применяется, лист не называет — сверить по "
                 "СЦиР-1982. 1 — не применяется"),
            coef(P_K_VEZ_265_9,
                 "ВЭЗ: коэффициент «т.265 п.9» — умножает стоимость ВЭЗ. В листе эталона 1,1 "
                 "(ячейка H17); условие лист не называет — сверить по СЦиР-1982. 1 — не применяется"),
            coef(P_K_MPV_252_6,
                 "МПВ: коэффициент «с.252 п.6» — умножает стоимость сейсморазведки МПВ (строки обоих "
                 "типов волн). В листе эталона 1,2 (ячейки H18, H19); условие лист не называет. Тот же "
                 "коэффициент у ВЭЗ применяется всегда (исторически), у МПВ — по выбору. "
                 "1 — не применяется"),
            coef(P_K_OFFICE_P22,
                 "Камеральные работы: коэффициент «П-22» — умножает программу работ, камеральную "
                 "обработку ВЭЗ и МПВ (технический отчёт — нет). В листе эталона 1,15 (ячейки H22, "
                 "G23, G24, G25); что такое «П-22» и условие, лист не расшифровывает (та же ссылка — "
                 "у метрологии и районного коэффициента). 1 — не применяется"),
            coef(P_K_OFFICE_MPV_NOTE2,
                 "Камеральная обработка МПВ: коэффициент «прим.2» (примечание 2 к т.291) — умножает "
                 "только камеральную обработку МПВ, вместе с «П-22». В листе эталона 1,15 (ячейки H24, "
                 "H25); условие лист не называет. 1 — не применяется"),
            Field(name=P_ORG_MODE, label="Организация и ликвидация работ — способ расчёта",
                  type="text", required=False, dynamic_options=True),
            # Без значения по умолчанию: пусто видно в форме и даёт
            # предупреждение «К не задан, применён 1» (раньше форма молча
            # показывала 1 при 1,2 в листе).
            Field(name=P_ORG_SHEET_K, type="number", required=False, label=
                 "Организация и ликвидация по формуле эталона: коэффициент К — умножает всю сумму "
                 "оргликвидации. Условие по подписи листа: «ОУ п.13, прим.1 (К=1,2 — приравнен к "
                 "районам Крайнего Севера)», то есть для районов, приравненных к Крайнему Северу; в "
                 "листе 1,2 (ячейка G31). Пусто — 1 (с предупреждением)"),
        ]

    def input_schema(self) -> list[Field]:
        return self.volume_schema() + self.estimate_schema()

    def field_options(self, field_name: str, refs: Any) -> list[tuple[str, str]]:
        if field_name == F_MPV_WAVES:
            return list(WAVES_OPTIONS)
        if field_name in (F_OFFICE_VEZ, F_REPORT):
            return list(YES_NO_OPTIONS)
        if field_name == P_ORG_MODE:
            return list(ORG_MODE_OPTIONS)
        return []

    # --- ведомость ---

    def propose_volumes(self, tz: TzParams) -> Proposal:
        """Черновик ввода этапа ведомости по параметрам ТЗ (core/tz.py, 03.11.2026).
        Объёмы ВЭЗ и сейсморазведки из ТЗ не выводятся (в ТЗ геофизика обычно «при
        необходимости»; длина профилей — решение исполнителя): предлагается только
        программа работ — одна на объект."""
        proposal = Proposal()
        proposal.set(F_PROGRAM, 1, KIND_RULE, "программа работ — одна на объект")
        proposal.leave("vez_count", "объём ВЭЗ (вертикальное электрическое зондирование) — "
                                    "решение исполнителя")
        proposal.leave("mpv_count", "объём сейсморазведки МПВ (метод преломлённых волн) — "
                                    "решение исполнителя")
        if tz.seismicity:
            proposal.notes.append(
                f"По ТЗ сейсмичность {tz.seismicity:g} баллов — для уточнения сейсмичности может "
                "понадобиться сейсморазведка МПВ; её объём — ввод оператора."
            )
        return proposal

    def key_volume_fields(self) -> list[Field]:
        """Ключевые объёмы раздела для режима «ключевые объёмы» (core/tz.py)."""
        return key_volumes.key_volume_fields()

    def key_volume_options(self, name: str) -> list[tuple[str, str]]:
        """Варианты выпадающего списка ключевого объёма (тип волн МПВ)."""
        return key_volumes.key_volume_options(name)

    def propose_from_key_volumes(self, tz: TzParams, keys: dict) -> Proposal:
        """Черновик ввода ведомости по ключевым объёмам: предложение по ТЗ +
        ключевые объёмы + достроенное по связям (modules/geophysics/key_volumes.py)."""
        return key_volumes.propose(self, tz, keys)

    def build_volumes(self, data: dict, refs: Any) -> dict:
        # Прежний одноэтапный вход сметы — числа из ведомости
        # (build_volume_sheet), без правок оператора.
        return {key: line.qty for key, line in self.build_volume_sheet(data).items()}

    def build_volume_sheet(
        self, data: dict, pinned: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, VolumeLine]:
        """Ведомость объёмов (этап 1). ВЭЗ и МПВ — прямой ввод оператора.
        Камералка МПВ — жёсткая связь (= объём МПВ своего типа волн);
        оператор может поправить её строкой ведомости, правка побеждает
        формулу (решение №6, docs/multistage_design.md). Программа,
        обработка ЭР, техотчёт — строки только если оператор их включил."""
        pinned = pinned or {}
        mpv_count = data.get("mpv_count", 0)
        waves = _waves(data)
        lines = {
            VK_VEZ: VolumeLine(
                VK_VEZ, "Вертикальное электрическое зондирование (ВЭЗ)", "ф.т",
                data.get("vez_count", 0), SOURCE_INPUT, "ввод оператора",
            ),
        }
        if waves == WAVES_BOTH:
            pairs = [(VK_MPV, VK_KAMERALKA_MPV, WAVE_NAMES[WAVES_P]),
                     (VK_MPV_S, VK_KAMERALKA_MPV_S, WAVE_NAMES[WAVES_S])]
        else:
            pairs = [(VK_MPV, VK_KAMERALKA_MPV, WAVE_NAMES[waves])]
        for field_key, _office_key, wave in pairs:
            lines[field_key] = VolumeLine(
                field_key, f"Сейсморазведка МПВ с поверхности ({wave})", "ф.н.",
                mpv_count, SOURCE_INPUT, "ввод оператора",
            )
        program = _number(data.get(F_PROGRAM), 0)
        if program > 0:
            lines[VK_PROGRAM] = VolumeLine(
                VK_PROGRAM, "Составление программы работ", "шт.", program, SOURCE_INPUT,
                "ввод оператора",
            )
        if data.get(F_OFFICE_VEZ) == YES:
            lines[VK_OFFICE_VEZ] = VolumeLine(
                VK_OFFICE_VEZ, "Камеральная обработка данных электроразведки (ВЭЗ)", "компл.", 1,
                SOURCE_INPUT, "ввод оператора; в смете — 30 % стоимости ВЭЗ",
            )
        for _field_key, office_key, wave in pairs:
            office = pinned.get(office_key, {})
            lines[office_key] = VolumeLine(
                office_key, f"Камеральная обработка данных МПВ ({wave})", "ф.н.",
                office.get("qty", mpv_count),
                SOURCE_OPERATOR if "qty" in office else SOURCE_CLASS_A,
                "= объём МПВ (жёсткая связь, лист №1.2.2_ИГФИ)",
            )
        if data.get(F_REPORT) == YES:
            lines[VK_REPORT] = VolumeLine(
                VK_REPORT, "Составление технического отчёта", "отчёт", 1, SOURCE_INPUT,
                "ввод оператора",
            )
        return lines

    # --- смета ---

    def build_estimate(
        self, volumes: dict, refs: Any, params: dict | None = None, waves: str | None = None
    ) -> dict[str, EstimateLine]:
        """Смета по ведомости. `params` — параметры этапа сметы
        (коэффициенты листа); нет/пусто — коэффициент 1 (не применяется).
        `waves` — тип волн из ввода ведомости (нужен только для подписи
        строк МПВ при «поперечных»: ведомость передаёт в смету одни объёмы);
        нет — подписи как до Э6в."""
        params = params or {}

        def coefs(*names: str) -> dict[str, float]:
            result = {}
            for name in names:
                key, _basis = COEF_PARAMS[name]
                value = _number(params.get(name), coef_default(name))
                if value != 1.0:
                    result[key] = value
            return result

        def basis_with(basis: str, line_coefs: dict[str, float]) -> str:
            labels = [label for name, (key, label) in COEF_PARAMS.items() if key in line_coefs]
            if not labels:
                return basis
            return f"{basis}; параметры сметы: {', '.join(labels)} (по листу эталона {SHEET})"

        lines: dict[str, EstimateLine] = {}

        vez_name = "Вертикальное электрическое зондирование (ВЭЗ)"
        vez_price, vez_basis = get_price_or_missing(refs, SOURCE, VEZ_TABLE, VEZ_ITEM, None, vez_name)
        vez_extra = coefs(P_K_VEZ_265_2, P_K_VEZ_265_9)
        lines["geophysics.vez"] = EstimateLine(
            id="geophysics.vez", name=vez_name, unit="ф.т", qty=volumes[VK_VEZ], price=vez_price,
            coefs={"k_252_6": VEZ_COEFFICIENT, **vez_extra}, kind="A", base_ids=[],
            basis=basis_with(vez_basis, vez_extra),
        )

        mpv_price, mpv_basis = get_price_or_missing(
            refs, SOURCE, MPV_TABLE, MPV_ITEM, None, "Сейсморазведка МПВ с поверхности"
        )
        mpv_coefs = coefs(P_K_MPV_252_6)
        field_ids = ["geophysics.vez"]
        # Ведомость передаёт в смету только {ключ: объём}, без названий: при
        # двух типах волн подписываем обе строки; при одних поперечных —
        # «(поперечные волны)», как в ведомости (ревью, 30.10.2026); при одних
        # продольных (по умолчанию) — без подписи, как до Э6в: так строки
        # сметы и их отпечаток у прежних смет не меняются.
        both = VK_MPV_S in volumes
        if both:
            first_suffix = " (продольные волны)"
        elif waves == WAVES_S:
            first_suffix = " (поперечные волны)"
        else:
            first_suffix = ""
        for key, suffix in ((VK_MPV, first_suffix), (VK_MPV_S, " (поперечные волны)")):
            if key not in volumes:
                continue
            line_id = f"geophysics.{key}"
            lines[line_id] = EstimateLine(
                id=line_id, name="Сейсморазведка МПВ с поверхности" + suffix, unit="ф.н.",
                qty=volumes[key], price=mpv_price, coefs=dict(mpv_coefs), kind="A", base_ids=[],
                basis=basis_with(mpv_basis, mpv_coefs),
            )
            field_ids.append(line_id)

        office_p22 = coefs(P_K_OFFICE_P22)
        report_base: list[str] = []
        if VK_PROGRAM in volumes:
            name = "Составление программы работ"
            price, basis = get_price_or_missing(refs, SOURCE, PROGRAM_TABLE, PROGRAM_ITEM, None, name)
            lines["geophysics.program"] = EstimateLine(
                id="geophysics.program", name=name, unit="шт.", qty=volumes[VK_PROGRAM], price=price,
                coefs=dict(office_p22), kind="A", base_ids=[], basis=basis_with(basis, office_p22),
            )
            report_base.append("geophysics.program")

        if VK_OFFICE_VEZ in volumes:
            k = office_p22.get("k_p22", 1.0)
            lines["geophysics.office_vez"] = EstimateLine(
                id="geophysics.office_vez", name="Камеральная обработка данных электроразведки (ВЭЗ)",
                unit="%", qty=0.0, price=0.0, coefs={"rate": OFFICE_VEZ_SHARE * k}, kind="B",
                base_ids=["geophysics.vez"],
                basis=f"стр.288: {OFFICE_VEZ_SHARE:g} от стоимости ВЭЗ (лист эталона {SHEET}, ячейка F23)"
                + (f" × {k:g} (К=1.15 П-22, ячейка G23)" if k != 1.0 else ""),
            )
            report_base.append("geophysics.office_vez")

        office_name = "Камеральная обработка данных МПВ"
        office_price, office_basis = get_price_or_missing(
            refs, SOURCE, KAMERALKA_MPV_TABLE, KAMERALKA_MPV_ITEM, None, office_name
        )
        office_coefs = {**office_p22, **coefs(P_K_OFFICE_MPV_NOTE2)}
        for key, suffix in ((VK_KAMERALKA_MPV, first_suffix), (VK_KAMERALKA_MPV_S, " (поперечные волны)")):
            if key not in volumes:
                continue
            line_id = f"geophysics.{key}"
            lines[line_id] = EstimateLine(
                id=line_id, name=office_name + suffix, unit="ф.н.", qty=volumes[key],
                price=office_price, coefs=dict(office_coefs), kind="A", base_ids=[],
                basis=basis_with(office_basis, office_coefs),
            )
            report_base.append(line_id)

        if VK_REPORT in volumes:
            name = "Составление технического отчёта"
            rate, basis = get_price_or_missing(refs, SOURCE, REPORT_TABLE, REPORT_ITEM, None, name)
            lines["geophysics.report_fixed"] = EstimateLine(
                id="geophysics.report_fixed", name=name + ": постоянная часть", unit="руб.",
                qty=volumes[VK_REPORT], price=REPORT_FIXED, coefs={}, kind="A", base_ids=[],
                basis=f"т.294 п.10 (лист эталона {SHEET}, ячейка E26; формула K26 = E26 + 10 % × "
                "камеральных; К=1,2 из H26 формулой листа не применяется)",
            )
            lines["geophysics.report"] = EstimateLine(
                id="geophysics.report", name=name + ": доля от камеральных работ", unit="%",
                qty=0.0, price=0.0, coefs={"rate": rate}, kind="B", base_ids=report_base,
                basis=f"{basis}; от программы и камеральной обработки (лист эталона {SHEET}, K26)",
            )

        # Внутренний транспорт — 5% (табл.4 п.1), ДРУГАЯ ставка, чем у
        # геологии (8.75%, табл.4 п.3) — из справочника, не константой.
        transport_name = "Расходы по внутреннему транспорту"
        transport_rate, transport_basis = get_price_or_missing(
            refs, SOURCE, TRANSPORT_TABLE, TRANSPORT_ITEM, None, transport_name
        )
        lines["geophysics.transport"] = EstimateLine(
            id="geophysics.transport", name=transport_name, unit="%", qty=0.0, price=0.0,
            coefs={"rate": transport_rate}, kind="B", base_ids=field_ids, basis=transport_basis,
        )
        return lines

    # --- организация и ликвидация по формуле листа ---

    @staticmethod
    def _org_liquidation_lines(estimate: dict[str, EstimateLine], k: float) -> dict[str, EstimateLine]:
        """K31 листа: ((F×3 % + 3000) + (F×0,025 % + 2500)) × К, F = полевые +
        внутренний транспорт — две строки: доля от F и постоянная часть."""
        org_id = accrual_line_id(ACCRUALS, ORG_LIQUIDATION)
        base_ids = [i for i in estimate if i in FIELD_IDS or i == "geophysics.transport"]
        source = (f"ОУ п.13 прим.1; лист эталона {SHEET}, формула ячейки K31, К — ячейка G31 "
                  f"(здесь К = {k:g})")
        return {
            org_id: EstimateLine(
                id=org_id, name="Организация и ликвидация работ: доля от полевых и вн. транспорта",
                unit="%", qty=0, price=0.0, coefs={"rate": ORG_SHEET_PERCENT * k}, kind="B",
                base_ids=base_ids,
                basis=f"(3 % + 0,025 %) × {k:g} от суммы полевых работ + внутреннего транспорта; {source}",
            ),
            ORG_FIXED_ID: EstimateLine(
                id=ORG_FIXED_ID, name="Организация и ликвидация работ: постоянная часть",
                unit="руб.", qty=1, price=ORG_SHEET_FIXED, coefs={"k": k}, kind="A", base_ids=[],
                basis=f"(3 000 + 2 500) × {k:g}; {source}",
            ),
        }

    @staticmethod
    def _has_field_work(estimate: dict[str, EstimateLine]) -> bool:
        """Есть ли полевые работы (база F формулы K31 не нулевая). По
        объёмам, а не по суммам: при ненайденной цене (0) оператор может
        ввести её правкой, и тогда F станет больше нуля."""
        return any((estimate[i].qty or 0) > 0 for i in FIELD_IDS if i in estimate)

    def estimate_with_accruals(
        self, volumes: dict, refs: Any, params: dict | None = None, waves: str | None = None
    ):
        """Смета + начисления уровня сметы (как на этапе 2)."""
        params = params or {}
        estimate = self.build_estimate(volumes, refs, params, waves=waves)
        if org_mode(params) != ORG_MODE_SHEET:
            return apply_accruals(estimate, ACCRUALS, refs)
        result = apply_accruals(estimate, ACCRUALS_WITHOUT_ORG, refs)
        if not self._has_field_work(estimate):
            # F = 0: в листе это строка без полевых работ — постоянную часть
            # 5 500 × К не начисляем (ревью, 30.10.2026).
            return result
        org = self._org_liquidation_lines(result, _number(params.get(P_ORG_SHEET_K), 1.0))
        regional_id = accrual_line_id(ACCRUALS, REGIONAL_FIELD)
        ordered: dict[str, EstimateLine] = {}
        for line_id, line in result.items():
            if line_id == regional_id:
                ordered.update(org)
                line = replace(line, base_ids=[*line.base_ids, *org])
            ordered[line_id] = line
        for line_id, line in org.items():
            ordered.setdefault(line_id, line)
        return ordered

    # --- многоэтапный сценарий (core/stages.py) ---

    def stages(self) -> list[Stage]:
        """Ведомость объёмов → смета (параметры — коэффициенты листа и способ
        расчёта оргликвидации)."""

        def compute_volumes(inputs, previous, refs, pinned):
            return self.build_volume_sheet(inputs, pinned)

        def compute_estimate(inputs, previous, refs, pinned):
            # Начисления (С3) — по ставкам данных объекта; не заданы — как раньше.
            volumes = previous["volumes"]
            return self.estimate_with_accruals(
                volumes.values(), refs, inputs, waves=_waves(dict(volumes.inputs))
            )

        def estimate_context_warnings(inputs, previous, refs):
            warnings = _coefficient_warnings(inputs)
            if org_mode(inputs) != ORG_MODE_SHEET:
                return warnings + accrual_warnings(ACCRUALS, refs)
            warnings += accrual_warnings(ACCRUALS_WITHOUT_ORG, refs)
            warnings += _org_sheet_warnings(inputs, previous, refs)
            return warnings

        # См. core/module_helpers.py (ввод — в форме; решение №5; ставка у
        # долей). Строка сметы «geophysics.<ключ>» ↔ строка ведомости «<ключ>».
        return [
            Stage(
                number=1,
                key="volumes",
                title="Ведомость объёмов работ",
                fields=self.volume_schema(),
                compute=compute_volumes,
                editable_fields=volume_sheet_editable,
                confirm_label="Подтвердить ведомость",
                context_warnings=_volume_zero_warnings,
            ),
            Stage(
                number=2,
                key="estimate",
                title="Смета",
                fields=self.estimate_schema(),
                compute=compute_estimate,
                # Все предупреждения модуля — о ценах/коэффициентах
                # расценок, то есть о смете, не о ведомости.
                check=self.check,
                editable_fields=estimate_editable_by(_estimate_line_volume_key),
                confirm_label="Подтвердить смету",
                context_warnings=estimate_context_warnings,
            ),
        ]

    def check(self, estimate: dict[str, EstimateLine]) -> list[str]:
        warnings = []

        for line in estimate.values():
            if not line.basis.startswith(MISSING_PRICE_BASIS_PREFIX):
                continue
            warnings.append(
                f"Цена для «{line.name}» не найдена в справочнике, "
                "требуется ручная проверка"
            )

        office = [line for key, line in estimate.items()
                  if key in ("geophysics.kameralka_mpv", "geophysics.kameralka_mpv_s")]
        if office and not any("k_p22" in line.coefs or "k_prim2" in line.coefs for line in office):
            warnings.append(
                "«Камеральная обработка данных МПВ»: коэффициенты листа эталона 1.15 × 1.15 "
                "(«К=1.15 П-22», «К=1.15 прим.2», ячейки G24/H24) не применены — задайте их в "
                "параметрах сметы, если они относятся к объекту (основание по бумажному "
                "СЦиР-1982 не сверено)"
            )

        vez = estimate.get("geophysics.vez")
        if vez is not None and not ({"k_265_2", "k_265_9"} & set(vez.coefs)):
            warnings.append(
                "«Вертикальное электрическое зондирование (ВЭЗ)»: из трёх коэффициентов листа "
                "эталона (1.2/1.15/1.1) применён только 1.2 (с.252 п.6) — 1.15 (т.265 п.2) и 1.1 "
                "(т.265 п.9) задаются в параметрах сметы, если относятся к объекту"
            )

        mpv = [line for key, line in estimate.items() if key in ("geophysics.mpv", "geophysics.mpv_s")]
        if mpv and not any("k_252_6" in line.coefs for line in mpv):
            warnings.append(
                "«Сейсморазведка МПВ»: коэффициент 1,2 «с.252 п.6» не применён, хотя в листе эталона он "
                "стоит и у МПВ (ячейки H18, H19), и у ВЭЗ (G17). К ВЭЗ программа применяет его всегда — "
                "так исторически посчитана контрольная цифра; к МПВ — только если задать его в "
                "параметрах сметы (по умолчанию 1). Какое правило верное, решает пользователь "
                "условие применения в листе не указано."
            )

        return warnings
