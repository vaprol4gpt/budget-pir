"""Сборщик сводного бюджета из готовых смет нескольких модулей.

Это НЕ новая расчётная логика: сборщик берёт уже посчитанный итог
каждого модуля (`core/pricing.py::calculate_total_with_vat`, свой
индекс и своя ставка НДС для каждого модуля) и агрегирует их, не
пересчитывая заново.

`indexed_total`/`vat_amount`/`total_with_vat` — `float | None`, а не
просто `float`: `calculate_total_with_vat` уже может вернуть их `None`,
если индекс/ставка НДС для этого модуля не найдены в справочнике на
дату расчёта (см. `core/pricing.py::TotalWithVat`): в строгом режиме
индекса — на любую дату вне внесённых периодов, в мягком — на дату
раньше всех периодов (сейчас индексы всех пяти модулей внесены только
на III кв. 2026; открытые вопросы — docs/known_risks.md, список вопросов).
`ModuleBudget` намеренно повторяет этот же паттерн `float | None`, а не
требует "додумать" число там, где его на самом деле нет.

Как устроена сводная смета компании (Э3/Э9, 30.10.2026; формулы листов
«№1ПС_Сводная» обеих книг `reference_data/`):

- раздел «Инженерные изыскания» — сумма итогов листов разделов, каждый уже
  в текущих ценах (× индекс, без НДС): Автодорога D18 `=D19+D20+D22+D23+D21`
  (ИГДИ G75, ИГИ H160, ИГФИ K35, ИГМИ F370, ИЭИ H114); Карьер D24 — то же
  (+ три листа ИГИ и ГРР отдельной строкой — у программы их нет);
- **первый уровень снижения — внутри листа раздела, после индекса**:
  №1.3_ИГМИ!F370 `=F369*D370`, D370 = 0,65 («Итого по смете с учётом
  понижающего коэффициента»); в сводную идёт уже сниженная сумма;
- **второй уровень — к ИТОГУ сводной** (изыскания + проектные разделы +
  экспертизы): Автодорога F36 `=F35*0.8` («понижающий коэффициент 0,8 —
  наличие ОПР и предварительных изысканий»), Карьер F39 `=F38*C39`, C39 =
  0,85; столбец «в КП» — каждая строка × тот же коэффициент (G18
  `=F18*$C$36`; у Карьера X — ROUND(…, 2));
- **НДС — после снижения**, на всю сниженную сумму: F37 `=F36*0.22`,
  ВСЕГО F38 = сниженный итог + НДС (Карьер F40/F41 — так же).

Программа повторяет это так: `ModuleBudget.reduction` — коэффициент
раздела (после индекса, к `indexed_total`, включая свои строки «после
индекса»), `BudgetAggregator.budget_reduction` — коэффициент сводной (к
сумме разделов после их снижения), НДС — от сниженной суммы. Оба по
умолчанию 1 — суммы прежние до копейки (умножение на 1.0 в float точно).
Коэффициент — ввод оператора (решение о цене), в эталонах компании у
снижения нет нормы: 0,65 / 0,8 / 0,85 — решения по объекту (В-27).

Мобилизация/демобилизация (`mobilization_base`, показ «в том числе»,
выключен по умолчанию) — в `reference_data/` её НЕТ; правило — только из
исходников заказчика 2026 г. (сводная «в т.ч. мобилизация/демобилизация» =
(внешний транспорт + оргликвидация) × районный × индекс, пополам). Это не
добавка, а выделение части уже посчитанного итога: итог от показа не
меняется. Районный программа берёт не константой листа заказчика, а из
самой сметы раздела — ставку тех строк районного коэффициента, в базу
которых входят строки транспорта/оргликвидации (норм не выдумываем)."""

from dataclasses import dataclass, field

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from core.excel_text import append_row
from core.models import EstimateLine, calculate_amount
from core.pricing import TotalWithVat

HEADERS = ["Раздел", "База (в ценах раздела)", "С индексацией", "НДС", "Итого с НДС"]
# Выгрузка при заданном снижении (хотя бы один коэффициент ≠ 1): как лист
# «№1ПС_Сводная» — до снижения, коэффициент, после снижения, затем строки
# итога сводной (коэффициент, сниженный итог, НДС, всего).
REDUCED_HEADERS = [
    "Раздел", "База (в ценах раздела)", "С индексацией (до снижения)",
    # «до снижения сводной» — чтобы не путать с итогом после обоих снижений
    # (ревью сводной №2; на экране — две колонки: раздела и раздела × сводной)
    "Коэффициент снижения раздела", "После снижения раздела (до снижения сводной)",
]
TOTAL_SECTIONS = "ИТОГО по разделам (до снижения сводной)"
TOTAL_BUDGET_COEF = "Коэффициент снижения сводной"
TOTAL_REDUCED = "Итого с учётом снижения (без НДС)"
TOTAL_ALL = "ВСЕГО с НДС"
MOBILIZATION = "в т.ч. мобилизация"
DEMOBILIZATION = "в т.ч. демобилизация"

# Лист «КП» (расчёт цены предложения, Э9): номера разделов — как в сводной компании
# («№1ПС_Сводная» эталона Автодороги: 1.1 ИГДИ … 1.4 ИЭИ).
KP_SHEET = "КП"
KP_NUMBERS = {"geodesy": "1.1", "geology": "1.2.1", "geophysics": "1.2.2",
              "hydromet": "1.3", "ecology": "1.4"}
KP_HEADERS = [
    "№ п/п", "Наименование работ", "Сметная стоимость без НДС (до снижения), руб.",
    "Коэффициент снижения", "Цена без НДС (после снижения), руб.", "НДС", "Цена с НДС, руб.",
]
KP_NOTE = (
    "Коэффициент снижения в строке раздела — общий: раздел × сводная. НДС — от цены после "
    "снижения. Сметная стоимость — по сборникам базовых цен с индексацией на дату расчёта."
)
# Оформление по дизайн-коду компании (docs/design/firm-design-code/tokens.json, светлая тема):
# шапка таблицы — surface-sunken, рамки — border, итог — accent-soft. Шрифт — Arial:
# фирменный Golos Text на ПК заказчика может отсутствовать.
KP_FONT = "Arial"
KP_HEADER_FILL = "E7ECEC"
KP_BORDER = "D3DBDA"
KP_TOTAL_FILL = "DDF1EE"
# Предупреждение «работа в убыток» под таблицей КП: danger / danger-soft (светлая тема).
KP_WARNING_COLOR = "B3261E"
KP_WARNING_FILL = "FBE6E4"
# Знаков в строке объединённой ячейки B:G (ширины столбцов 44+20+13+20+18+20, жирный 10 pt).
KP_WARNING_CHARS_PER_LINE = 110
MONEY_FORMAT = "#,##0.00"

# Последний сегмент id строк сметы: транспорт и организация/ликвидация
# (core/accruals.py `<модуль>.accrual.<имя>`, у геофизики ещё
# постоянная часть оргликвидации, у ИГМИ — свои строки с тем же именем) и
# районный коэффициент (accruals + ИГМИ «field_regional»/«kameral_regional»).
MOBILIZATION_LINES = ("external_transport", "org_liquidation", "org_liquidation_fixed")
REGIONAL_LINES = ("regional_field", "regional_office", "field_regional", "kameral_regional")


def _last(line_id: str) -> str:
    return line_id.rsplit(".", 1)[-1]


def mobilization_base(estimate: dict[str, EstimateLine]) -> float:
    """Внешний транспорт + организация и ликвидация раздела с районным
    коэффициентом, начисленным на них, — в базовых ценах раздела (до
    индекса). Районный — только доля строк районного коэффициента, в базу
    которых входят эти строки (ставка × сумма строки), как в смете. Нет
    таких строк — 0."""
    cache: dict[str, float] = {}
    mob = {i for i in estimate if _last(i) in MOBILIZATION_LINES}
    total = sum(calculate_amount(estimate[i], estimate, cache) for i in mob)
    for line in estimate.values():
        if _last(line.id) not in REGIONAL_LINES or line.kind not in ("B", "C"):
            continue
        rate = line.coefs.get("rate", 0.0)
        total += sum(rate * calculate_amount(estimate[b], estimate, cache)
                     for b in line.base_ids if b in mob)
    return total


def _style_kp(ws, header_row: int, group_row: int, total_row: int) -> None:
    """Оформление листа «КП»: заголовок, шапка с заливкой, рамки у таблицы,
    деньги — с разделителями разрядов, коэффициент — 4 знака, итог — заливкой."""
    side = Side(style="thin", color=KP_BORDER)
    border = Border(left=side, right=side, top=side, bottom=side)
    for row in ws.iter_rows():
        for cell in row:
            cell.font = Font(name=KP_FONT, size=10)
    ws["A1"].font = Font(name=KP_FONT, size=12, bold=True)
    for row in ws.iter_rows(min_row=header_row, max_row=total_row, max_col=len(KP_HEADERS)):
        for cell in row:
            cell.border = border
            if cell.column in (3, 5, 6, 7):
                cell.number_format = MONEY_FORMAT
            elif cell.column == 4:
                cell.number_format = "0.0000"
            if cell.row == header_row:
                cell.font = Font(name=KP_FONT, size=10, bold=True)
                cell.fill = PatternFill("solid", fgColor=KP_HEADER_FILL)
                cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            elif cell.row in (group_row, total_row):
                cell.font = Font(name=KP_FONT, size=10, bold=True)
                if cell.row == total_row:
                    cell.fill = PatternFill("solid", fgColor=KP_TOTAL_FILL)
    ws.cell(row=total_row + 2, column=4).number_format = "0.0000"
    for letter, width in zip("ABCDEFG", (8, 44, 20, 13, 20, 18, 20)):
        ws.column_dimensions[letter].width = width


def _rub(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " руб."


def _num(value: float, digits: int | None = None) -> str:
    """Коэффициент с десятичной запятой: «0,8», «1,098»."""
    text = f"{value:.{digits}f}" if digits is not None else f"{value:g}"
    return text.replace(".", ",")


def validate_reduction(value: float) -> float:
    """Коэффициент снижения: 0 < k ≤ 1 (1 — без снижения)."""
    value = float(value)
    if not 0 < value <= 1:
        raise ValueError(
            f"Коэффициент снижения {value:g}: ожидается число больше 0 и не больше 1 "
            "(1 — без снижения, 0,8 — снижение на 20 %)."
        )
    return value


@dataclass
class ModuleBudget:
    module_code: str
    module_name: str
    base_total: float
    indexed_total: float | None
    vat_amount: float | None
    total_with_vat: float | None
    warnings: list[str] = field(default_factory=list)
    # Статус последнего этапа модуля (core/stages.py). В бюджет идут
    # только модули с подтверждённым и не устаревшим последним этапом
    # (решение №10, docs/multistage_design.md). По умолчанию True/False —
    # одноэтапный модуль, посчитанный вызывающим кодом напрямую
    # (build_volumes/build_estimate, как run_budget_stub.py), шага
    # подтверждения не имеет и считается готовым, как и раньше.
    confirmed: bool = True
    stale: bool = False
    # Применённый индекс и его период («III кв. 2026») — для показа в
    # сводном бюджете; None — индекс не найден / не передан.
    index: float | None = None
    index_period: str | None = None
    # Ставка НДС с основанием (core/pricing.py::TotalWithVat.vat_label).
    vat_label: str | None = None
    # Свои строки оператора «после индекса» (Э7, уже в текущих ценах) — уже
    # внутри indexed_total/total_with_vat; отдельно — только для показа.
    after_index_total: float = 0.0
    # Коэффициент снижения раздела (Э9): после индекса, к indexed_total
    # (как №1.3_ИГМИ!F370 = F369 × 0,65). 1 — без снижения.
    reduction: float = 1.0
    # Внешний транспорт + оргликвидация с районным, в базовых ценах раздела
    # (`mobilization_base`) — только для показа «в т.ч. мобилизация».
    mobilization_base: float = 0.0
    # Индекс взят не на дату расчёта, а последний известный (TotalWithVat.index_fallback).
    index_fallback: bool = False
    # … и отстал от даты расчёта больше чем на квартал (TotalWithVat.index_stale).
    index_stale: bool = False
    # Что снижает надёжность итога раздела и должно быть видно в сводной и на листе «КП»:
    # `notes` — тексты (например, не заданы ставки начислений), `assumptions` — сколько
    # параметров посчитано по правилам-допущениям (core/param_rules.py).
    notes: list[str] = field(default_factory=list)
    assumptions: int = 0
    # Шифр объекта, для которого подтверждена смета раздела («» — шифр не задан или
    # неизвестен). Разделы разных объектов в одну сводную не складываются
    # (`BudgetAggregator.mixed_objects`).
    object_code: str = ""

    @property
    def included(self) -> bool:
        return self.confirmed and not self.stale

    def _reduced(self, value: float | None) -> float | None:
        return None if value is None else value * self.reduction

    @property
    def reduced_total(self) -> float | None:
        """С индексацией после снижения раздела, без НДС."""
        return self._reduced(self.indexed_total)

    @property
    def reduced_vat_amount(self) -> float | None:
        return self._reduced(self.vat_amount)

    @property
    def reduced_total_with_vat(self) -> float | None:
        return self._reduced(self.total_with_vat)

    @property
    def mobilization(self) -> float | None:
        """Мобилизация + демобилизация раздела в текущих ценах после снижения
        раздела (без НДС); None — нет индекса."""
        if self.index is None:
            return None
        return self.mobilization_base * self.index * self.reduction

    @classmethod
    def from_totals(
        cls,
        module_code: str,
        module_name: str,
        totals: TotalWithVat,
        confirmed: bool = True,
        stale: bool = False,
        reduction: float = 1.0,
        mobilization_base: float = 0.0,
        notes: list[str] | None = None,
        assumptions: int = 0,
        object_code: str = "",
    ) -> "ModuleBudget":
        """Переупаковывает уже посчитанный `TotalWithVat` в `ModuleBudget` —
        ничего не пересчитывает заново."""
        return cls(
            module_code=module_code,
            module_name=module_name,
            base_total=totals.base_total,
            indexed_total=totals.indexed_total,
            vat_amount=totals.vat_amount,
            total_with_vat=totals.total_with_vat,
            warnings=list(totals.warnings),
            confirmed=confirmed,
            stale=stale,
            index=totals.index,
            index_period=getattr(totals, "index_period", None),
            vat_label=getattr(totals, "vat_label", None),
            after_index_total=getattr(totals, "after_index_total", 0.0) or 0.0,
            reduction=validate_reduction(reduction),
            mobilization_base=mobilization_base,
            index_fallback=getattr(totals, "index_fallback", False),
            index_stale=getattr(totals, "index_stale", False),
            notes=list(notes or []),
            assumptions=assumptions,
            object_code=(object_code or "").strip(),
        )


@dataclass(frozen=True)
class TargetFit:
    """Результат подбора коэффициента сводной под целевую цену
    (`BudgetAggregator.fit_budget_reduction`): `coefficient` — подобранный k
    (None — подобрать нельзя), `target` — целевая цена с НДС, `before` — итог с
    НДС при k = 1, `residual` — целевая минус итог при подобранном k (руб.),
    `message` — пояснение, если снижение не нужно или невозможно."""
    coefficient: float | None
    target: float
    before: float | None
    residual: float | None
    message: str | None


class BudgetAggregator:
    """Сводный бюджет объекта — по одному `ModuleBudget` на модуль.

    `budget_reduction` — коэффициент снижения сводной (к сумме разделов
    после их снижения, до НДС); 1 — без снижения. `show_mobilization` —
    показывать «в т.ч. мобилизация/демобилизация» в выгрузке (итог не
    меняется)."""

    def __init__(self, budget_reduction: float = 1.0, show_mobilization: bool = False) -> None:
        self._modules: list[ModuleBudget] = []
        self.budget_reduction = validate_reduction(budget_reduction)
        self.show_mobilization = show_mobilization
        # Уровень объекта для `reliability_notes`: допущения данных объекта и тексты.
        self.object_assumptions = 0
        self.object_notes: list[str] = []

    def reliability_notes(self) -> list[str]:
        """Что снижает надёжность итога — одним списком для страницы сводного бюджета,
        листа «КП» и листа «Сводный бюджет» (этап В карты, аудит 05.11.2026, С-1, С-2):
        индекс не на дату расчёта, прочие предупреждения итога, раздел без начислений,
        число допущений. Одинаковые тексты разделов объединены. Пусто — отмечать нечего."""
        included = [m for m in self._modules if m.included]
        by_text: dict[str, list[str]] = {}
        by_period: dict[str, list[str]] = {}
        for m in included:
            if m.index_fallback:
                by_period.setdefault(m.index_period or "последний известный", []).append(m.module_name)
            for text in [*m.warnings, *m.notes]:
                if m.index_fallback and text.startswith("Индекс "):
                    continue  # уже в строке об индексе ниже
                by_text.setdefault(text, []).append(m.module_name)
        stale = any(m.index_stale for m in included)
        notes = [
            f"Индекс не на дату расчёта: применён {period}, актуальный не внесён в справочник — "
            f"{', '.join(names)}. "
            + ("Индекс отстал больше чем на квартал: цена может заметно отличаться от текущей."
               if stale else "Цена может отличаться от текущей.")
            for period, names in by_period.items()
        ]
        notes += [f"{', '.join(names)}: {text}" for text, names in by_text.items()]
        notes += self.object_notes
        counts = [f"{m.module_name} — {m.assumptions}" for m in included if m.assumptions]
        if self.object_assumptions:
            counts.append(f"данные объекта — {self.object_assumptions}")
        total = sum(m.assumptions for m in included) + self.object_assumptions
        if total:
            word = ("допущение" if total % 10 == 1 and total % 100 != 11 else
                    "допущения" if total % 10 in (2, 3, 4) and total % 100 not in (12, 13, 14) else
                    "допущений")
            notes.append(
                f"Итог содержит {total} {word} в параметрах смет: {'; '.join(counts)}. Это значения "
                "программы до ответа специалистов (справочник правил параметров, страница "
                "«Справочники»)."
            )
        return notes

    def mixed_objects(self) -> str | None:
        """Текст «разделы разных объектов» — если у разделов, вошедших в бюджет, разные
        шифры объекта; None — объект один. Раздел без шифра конфликтом не считается
        (шифр не задан — не значит «другой объект»; пустой шифр у всех — не ошибка).
        Такой бюджет не собирается: страница показывает текст вместо итога, `to_excel`
        отказывает (аудит 05.11.2026, смешение объектов в сводной)."""
        by_code: dict[str, list[str]] = {}
        for m in self._modules:
            if m.included and m.object_code:
                by_code.setdefault(m.object_code, []).append(m.module_name)
        if len(by_code) < 2:
            return None
        parts = "; ".join(f"шифр «{code}» — {', '.join(names)}" for code, names in by_code.items())
        return (
            f"Разделы посчитаны для разных объектов ({parts}). Сводный бюджет не собран: "
            "откройте разделы одного объекта (страница «Версии», «Открыть все разделы "
            "расчёта») или пересчитайте и подтвердите сметы лишних разделов для текущего объекта."
        )

    def add_module(self, module_budget: ModuleBudget) -> None:
        """Добавить итог модуля. Повтор того же `module_code` — `ValueError`
        (решение Э2 дорожной карты, 25.10.2026): молча проигнорировать или
        заменить — значит скрыть ошибку вызывающего кода, а сложить дважды —
        удвоить модуль в бюджете."""
        if any(m.module_code == module_budget.module_code for m in self._modules):
            raise ValueError(
                f"Модуль '{module_budget.module_code}' уже добавлен в сводный бюджет"
            )
        validate_reduction(module_budget.reduction)
        self._modules.append(module_budget)

    def vat_labels(self) -> list[str]:
        """Ставки НДС с основаниями у модулей, вошедших в бюджет, без
        повторов (на одну дату расчёта — одна ставка у всех)."""
        return list(dict.fromkeys(
            m.vat_label for m in self._modules if m.included and m.vat_label
        ))

    def has_reduction(self) -> bool:
        """Задан ли хоть один коэффициент снижения (≠ 1)."""
        return self.budget_reduction != 1.0 or any(
            m.reduction != 1.0 for m in self._modules if m.included
        )

    def summary(self) -> dict:
        """Сводка по всем добавленным модулям.

        `base_total_sum` — сумма `base_total` по ВСЕМ модулям (эта
        цифра всегда есть, даже если для модуля не найден индекс).
        **Осторожно с интерпретацией:** `base_total` каждого модуля —
        в СВОЕЙ исторической базе цен (у геологии/геофизики — 1991 г.,
        у геодезии — 2004 г., см. записи разработки)
        — эта сумма складывает разные по факту "валюты", смешивать их
        напрямую экономически некорректно, но метод не пытается это
        как-то нормализовать или предупреждать — просто складывает,
        как есть. В отличие от `base_total`, поля ПОСЛЕ индексации
        (`indexed_total`, `vat_amount`, `total_with_vat`) у всех
        модулей уже в ценах ОДНОЙ и той же текущей даты расчёта —
        индекс переводит из СВОЕЙ исторической базы в текущие рубли,
        поэтому суммировать ИХ между модулями с разной базой цен
        корректно. `total_with_vat_sum` — итог с НДС по модулям, где он
        посчитан (не `None`), ПОСЛЕ обоих снижений (без снижения — просто
        сумма `total_with_vat`); `excluded_from_total` называет модули, не
        попавшие в эту сумму, и почему (их собственные `warnings`) — не
        гадаем за них итог, а честно показываем, что он недоступен.

        Снижение (Э9): `indexed_total_sum` — сумма разделов с индексацией до
        снижения; `sections_total` — после снижения разделов (= «ИТОГО по
        разделам» сводной); `budget_reduction`; `reduced_total` —
        `sections_total` × коэффициент сводной (без НДС); `vat_amount_sum` —
        НДС от сниженной суммы. `mobilization` / `mobilization_reduced` —
        мобилизация + демобилизация (в т.ч., до и после снижения сводной).

        Модули с неподтверждённым или устаревшим последним этапом в
        бюджет не входят вовсе (ни в одну сумму, ни в `modules`) —
        они перечислены в `not_confirmed` с причиной."""
        modules = [m for m in self._modules if m.included]
        base_total_sum = sum(m.base_total for m in modules)
        priced = [m for m in modules if m.total_with_vat is not None]
        excluded = [m for m in modules if m.total_with_vat is None]
        k = self.budget_reduction
        total_with_vat_sum = sum(m.reduced_total_with_vat * k for m in priced)
        sections_total = sum(m.reduced_total for m in priced)
        mobilization = sum(m.mobilization or 0.0 for m in priced)

        return {
            "modules": modules,
            "base_total_sum": base_total_sum,
            "indexed_total_sum": sum(m.indexed_total for m in priced),
            "sections_total": sections_total,
            "budget_reduction": k,
            "reduced_total": sections_total * k,
            "vat_amount_sum": sum(m.reduced_vat_amount * k for m in priced),
            "total_with_vat_sum": total_with_vat_sum,
            "mobilization": mobilization,
            "mobilization_reduced": mobilization * k,
            "excluded_from_total": [
                {"module_code": m.module_code, "module_name": m.module_name, "warnings": m.warnings}
                for m in excluded
            ],
            "not_confirmed": [
                {
                    "module_code": m.module_code,
                    "module_name": m.module_name,
                    "reason": "последний этап устарел" if m.confirmed else "последний этап не подтверждён",
                }
                for m in self._modules
                if not m.included
            ],
        }

    def final_price(self, module_code: str) -> float | None:
        """Цена раздела после обоих снижений, без НДС; None — модуля нет в
        бюджете или у него нет итога с индексацией."""
        for m in self._modules:
            if m.module_code == module_code and m.included and m.reduced_total is not None:
                return m.reduced_total * self.budget_reduction
        return None

    def total_before_budget_reduction(self) -> float | None:
        """Итог с НДС после снижения разделов, но БЕЗ снижения сводной (то же,
        что «Итого по объекту с НДС» при коэффициенте сводной 1); None — ни у
        одного вошедшего модуля нет итога с НДС."""
        priced = [m for m in self._modules if m.included and m.total_with_vat is not None]
        if not priced:
            return None
        return sum(m.reduced_total_with_vat for m in priced)

    def fit_budget_reduction(self, target_with_vat: float) -> "TargetFit":
        """Подбор коэффициента снижения сводной под целевую цену с НДС (Э9).

        Итог с НДС линеен по коэффициенту сводной k: каждая строка раздела
        (после снижения раздела) × k, НДС — от суммы после снижения (лист
        «№1ПС_Сводная»: F36 = F35 × k, F37 = F36 × ставка, F38 = F36 + F37), т.е.
        «Итого с НДС»(k) = k × «Итого с НДС»(1). Отсюда k = целевая / итог с НДС
        при k = 1 (`total_before_budget_reduction`), снижение разделов уже
        внутри. Коэффициент НЕ округляется: у компании он хранится полной точностью
        (в листе КП заказчика — 16 знаков, подобран под круглую цену; разбор КП,
        внутренний разбор исходных документов), поэтому итог совпадает с целевой до
        погрешности float (доли копейки) — `residual` её показывает.

        Целевая не ниже итога без снижения — k = 1 и сообщение «снижение не
        нужно» (повышать цену коэффициентом программа не умеет: k ≤ 1)."""
        target = float(target_with_vat)
        if not target > 0:
            raise ValueError(f"Целевая цена {target:g}: ожидается положительное число рублей.")
        before = self.total_before_budget_reduction()
        if before is None or before <= 0:
            return TargetFit(None, target, before, None,
                             "Подобрать нельзя: ни у одного раздела в бюджете нет итога с НДС.")
        if target >= before:
            what = "равна итогу" if target == before else "выше итога"
            return TargetFit(
                1.0, target, before, target - before,
                f"Снижение не нужно: целевая цена {_rub(target)} {what} сметы без снижения "
                f"сводной ({_rub(before)}). Коэффициент сводной — 1 (цену выше сметы "
                "коэффициентом не получить).",
            )
        k = target / before
        fitted = sum(
            m.reduced_total_with_vat * k
            for m in self._modules if m.included and m.total_with_vat is not None
        )
        return TargetFit(k, target, before, target - fitted, None)

    def to_excel(self, path, info: dict | None = None, kp_warnings: list[str] | None = None) -> None:
        """Одна строка на модуль + строка "ИТОГО" внизу.

        `info` — пары «подпись: значение» (дата расчёта, политика индекса,
        данные объекта) на второй лист «Сведения»; туда же — индексы
        модулей, предупреждения и модули, не вошедшие в бюджет. Без `info`
        второй лист всё равно есть, если есть что показать.

        Для модуля без посчитанного индекса/НДС (`None`) ячейки
        "С индексацией"/"НДС"/"Итого с НДС" остаются пустыми — не
        заполняются нулём или базовой суммой, чтобы не выглядело так,
        будто индекс/НДС для него посчитан и равен этому значению.

        Задано снижение (`has_reduction`) — лист как «№1ПС_Сводная»: по
        модулю — до снижения, коэффициент раздела, после снижения; под
        таблицей — ИТОГО по разделам, коэффициент сводной, итог с учётом
        снижения, НДС (от сниженной суммы), ВСЕГО с НДС. Без снижения —
        прежний вид. `show_mobilization` — ещё строки «в т.ч. мобилизация»
        и «в т.ч. демобилизация» (пополам, итог не меняют). Есть хоть один
        раздел с итогом — ещё лист «КП» (`_kp_sheet`): цена предложения по
        разделам, итог = «ВСЕГО с НДС»; `kp_warnings` — предупреждения под
        таблицей КП (например, «Работа в убыток на N руб.», core/target_loss.py).
        `reliability_notes` агрегатор добавляет сам — и на лист «КП», и под таблицу
        первого листа: выгрузить цену без этих отметок нельзя. Разделы разных объектов
        (`mixed_objects`) — `ValueError`: такой сводной быть не должно."""
        mixed = self.mixed_objects()
        if mixed:
            raise ValueError(mixed)
        wb = Workbook()
        ws = wb.active
        ws.title = "Сводный бюджет"
        included = [m for m in self._modules if m.included]
        summary = self.summary()
        priced = [m for m in included if m.total_with_vat is not None]

        if self.has_reduction():
            self._reduced_sheet(ws, included, summary, bool(priced))
        else:
            self._plain_sheet(ws, included)
        if self.show_mobilization and priced:
            half = summary["mobilization_reduced"] / 2
            # Значение без НДС — в колонку итога без НДС: «С индексацией» (обычный
            # вид) или «После снижения» (вид сводной), не под «Итого с НДС» (ревью Э9, №3).
            column = 4 if self.has_reduction() else 2
            for label in (MOBILIZATION, DEMOBILIZATION):
                row = [f"{label} (из итога выше, без НДС)", None, None, None, None]
                row[column] = half
                append_row(ws, row)

        notes = [("Ставка НДС", label) for label in self.vat_labels()]
        for module_budget in included:
            period = f" ({module_budget.index_period})" if module_budget.index_period else ""
            if module_budget.index is not None:
                notes.append((f"Индекс: {module_budget.module_name}", f"{module_budget.index}{period}"))
            if module_budget.after_index_total:
                notes.append((
                    f"Работы вне сборника после индекса: {module_budget.module_name}",
                    round(module_budget.after_index_total, 2),
                ))
            for warning in module_budget.warnings:
                notes.append((f"Внимание: {module_budget.module_name}", warning))
        for item in summary["not_confirmed"]:
            notes.append((f"Не вошёл в бюджет: {item['module_name']}", item["reason"]))
        notes.append((
            "База в строке ИТОГО",
            "не заполняется: базы цен разделов разные (1991, 2004, 2000 гг.), их сумма "
            "экономически некорректна; базы — по разделам, итог объекта — в текущих ценах "
            "(«С индексацией», «Итого с НДС»)",
        ))
        if self.has_reduction():
            notes.append((
                "Снижение цены",
                "как в сводной смете компании («№1ПС_Сводная»): коэффициент раздела — после индекса, "
                "коэффициент сводной — к сумме разделов; НДС — от суммы после снижения",
            ))
        if self.show_mobilization:
            notes.append((
                "Мобилизация/демобилизация",
                "выделение «в том числе» (итог не меняется): внешний транспорт + организация и "
                "ликвидация с начисленным на них районным, × индекс × снижение, пополам. Правило "
                "— из смет заказчика 2026 г., нормы нет",
            ))
        reliability = self.reliability_notes()
        if reliability:
            # Сразу под таблицей: кто открыл файл, видит это без перехода на другой лист.
            append_row(ws, [])
            for note in reliability:
                append_row(ws, [f"Внимание: {note}"])
        if priced:
            self._kp_sheet(wb.create_sheet(KP_SHEET), priced, summary, [*(kp_warnings or []), *reliability])
        if info or notes:
            ws_info = wb.create_sheet("Сведения")
            for label, value in (info or {}).items():
                append_row(ws_info, [label, value])
            for label, value in notes:
                append_row(ws_info, [label, value])

        wb.save(path)

    @staticmethod
    def _plain_sheet(ws, included) -> None:
        """Прежний вид (без снижения)."""
        append_row(ws, HEADERS)
        for module_budget in included:
            append_row(ws, [
                module_budget.module_name,
                module_budget.base_total,
                module_budget.indexed_total,
                module_budget.vat_amount,
                module_budget.total_with_vat,
            ])

        # ИТОГО — только по модулям с итогом с НДС (вошедшим в итог), чтобы
        # колонки строки ИТОГО сходились (ревью сводной №4).
        priced = [m for m in included if m.total_with_vat is not None]
        indexed_total_sum = sum(m.indexed_total for m in priced)
        vat_amount_sum = sum(m.vat_amount for m in priced)
        total_with_vat_sum = sum(m.total_with_vat for m in priced)
        if not priced:
            # Ни у одного модуля нет итога с индексом — пустые ячейки, не 0
            # (см. докстринг: не выглядеть посчитанным).
            indexed_total_sum = vat_amount_sum = total_with_vat_sum = None

        # Находка ревью №8 (принято по умолчанию 30.10.2026, список вопросов): базы
        # модулей — в ценах разных лет (1991, 2004, 2000), их сумма экономически
        # бессмысленна — в строке ИТОГО ячейка базы пустая, базы — только по
        # модулям; итог — в текущих ценах.
        append_row(ws, ["ИТОГО", None, indexed_total_sum, vat_amount_sum, total_with_vat_sum])

    def _kp_sheet(self, ws, priced, summary, warnings=()) -> None:
        """Лист «КП» — расчёт цены предложения, как приложение к КП компании
        (разбор КП, внутренний разбор исходных документов; лист «КП» и столбец «в КП»
        листа «№1ПС_Сводная» эталона Автодороги: каждая строка × коэффициент,
        НДС — от цены после снижения). Только то, что считает программа: без
        реквизитов, сроков и проектной части. Строки — разделы изысканий по
        нумерации сводной компании (`KP_NUMBERS`), коэффициент — общий (раздел ×
        сводная). Итог листа = «Итого по объекту с НДС» сводной (`summary`)."""
        k = self.budget_reduction
        vat_label = ", ".join(self.vat_labels()) or "НДС"
        append_row(ws, ["Расчёт цены предложения (инженерные изыскания)"])
        append_row(ws, [])
        append_row(ws, KP_HEADERS[:5] + [f"НДС ({vat_label})" if self.vat_labels() else "НДС",
                                         KP_HEADERS[6]])
        header_row = ws.max_row
        rows = sorted(priced, key=lambda m: KP_NUMBERS.get(m.module_code, "9"))
        append_row(ws, [
            "1", "Инженерные изыскания, в том числе:", summary["indexed_total_sum"], None,
            summary["reduced_total"], summary["vat_amount_sum"], summary["total_with_vat_sum"],
        ])
        group_row = ws.max_row
        for m in rows:
            append_row(ws, [
                KP_NUMBERS.get(m.module_code, "—"), m.module_name,
                m.indexed_total, m.reduction * k, m.reduced_total * k,
                m.reduced_vat_amount * k, m.reduced_total_with_vat * k,
            ])
        if self.show_mobilization:
            half = summary["mobilization_reduced"] / 2
            append_row(ws, [None, f"{MOBILIZATION} (из цены выше)", None, None, half, None, None])
            append_row(ws, [None, f"{DEMOBILIZATION} (из цены выше)", None, None, half, None, None])
        append_row(ws, [
            None, "ИТОГО", summary["indexed_total_sum"], None, summary["reduced_total"],
            summary["vat_amount_sum"], summary["total_with_vat_sum"],
        ])
        total_row = ws.max_row
        append_row(ws, [])
        append_row(ws, [None, "Коэффициент снижения сводной", None, k])
        append_row(ws, [None, KP_NOTE])
        _style_kp(ws, header_row, group_row, total_row)
        for warning in warnings:
            # Строка на всю ширину таблицы (B:G), с переносом и высотой под текст
            # (ревью трека 1, №6): ширина B:G ≈ 135 знаков, 10 pt ≈ 13 pt на строку.
            append_row(ws, [None, warning])
            row = ws.max_row
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=len(KP_HEADERS))
            cell = ws.cell(row=row, column=2)
            cell.font = Font(name=KP_FONT, size=10, bold=True, color=KP_WARNING_COLOR)
            cell.fill = PatternFill("solid", fgColor=KP_WARNING_FILL)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = 14 * (len(warning) // KP_WARNING_CHARS_PER_LINE + 1)

    def _reduced_sheet(self, ws, included, summary, any_priced: bool) -> None:
        """Вид со снижением — как лист «№1ПС_Сводная»."""
        append_row(ws, REDUCED_HEADERS)
        for m in included:
            append_row(ws, [m.module_name, m.base_total, m.indexed_total, m.reduction, m.reduced_total])
        value = (lambda key: summary[key]) if any_priced else (lambda key: None)
        vat_label = ", ".join(self.vat_labels()) or "НДС"
        append_row(ws, [TOTAL_SECTIONS, None, value("indexed_total_sum"), None, value("sections_total")])
        append_row(ws, [TOTAL_BUDGET_COEF, None, None, self.budget_reduction, None])
        append_row(ws, [TOTAL_REDUCED, None, None, None, value("reduced_total")])
        append_row(ws, [vat_label, None, None, None, value("vat_amount_sum")])
        append_row(ws, [TOTAL_ALL, None, None, None, value("total_with_vat_sum")])
