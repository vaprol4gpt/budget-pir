"""Себестоимость (минимальный бюджет) инженерно-геологических изысканий —
вариант Б, второй контур рядом со сметой по СБЦ-99 (Э6 дорожной карты,
25.10.2026). Расчёт СБЦ (`module.py`) не трогает.

Ресурсный расчёт: продолжительность полевых работ из метража и нормы
выработки → численность партии × оклады × месяцы (ФОТ) → прямые затраты
(ФОТ, соцотчисления, полевое довольствие, жильё, связь, быт, проезд,
топливо, трал, буровой инструмент и запчасти, сторонние лабораторные,
амортизация, командировочные) → накладные и налоги от ФОТ → резерв →
себестоимость. Нормы — `data/norms/geology_cost.json` (все ДОПУЩЕНИЯ с
источником). Оклады и цены топлива — только ввод оператора, без значений
по умолчанию: пока они не заполнены там, где нужны, расчёт помечен
неполным и коэффициент снижения не считается.

С 03.11.2026 («два дня») общий каркас — статьи, замыкание расчёта
(накладные, налоги, резерв), коэффициент снижения, объяснение «минимум >
максимума», статус калибровки — в `core/cost.py` (им же считаются
геодезия, геофизика, экология, гидрометеорология); здесь — только
драйверы геологии (метраж → бригады → срок, трал, буровой инструмент).
Числа геологии при переносе не изменились (тесты `test_geology_cost.py`,
`test_cost_guard.py`, `test_minimum_calibration.py` — прежние значения).
Имена, которые раньше жили здесь (`Reduction`, `reduction`, `money`, …),
импортируются отсюда по-прежнему.

Коэффициент снижения = себестоимость / сметная стоимость по СБЦ (с
индексацией, без НДС — себестоимость тоже без НДС). Альтернатива
знаменателя (Форма 2.2) — вопрос пользователю (список вопросов).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from core.cost import (  # noqa: F401 — реэкспорт: прежние имена модуля геологии
    ARTICLE_FIXED,
    ARTICLE_SHARE,
    ARTICLE_VOLUME,
    CALIBRATION_NOTE,
    RATE_ACCOUNTING,
    RATE_LOCAL,
    RATE_MISSING,
    RATE_OPERATOR,
    RATE_ORIGIN_LABELS,
    RATE_PRICE,
    RATE_SEED,
    REDUCTION_IMPOSSIBLE,
    ArticleShare,
    CostLine,
    CostResult as _CostResult,
    RateOrigin,
    RatesStatus,
    Reduction,
    SharedCosts,
    close_cost,
    coefficient_text,
    cost_structure,
    fixed_costs,
    fot_share_text,
    load_cost_norms,
    loss_explanation,
    money,
    percent,
    rates_status_for,
    reduction,
    shared_lines,
    top_articles,
)
from core.norms import LocalValue, Norm

NORMS_NAME = "geology_cost"

# Должности полевой партии: (ключ, подпись). Численность буровых рабочих —
# бригады × норма «workers_per_brigade», остальные — ввод.
FIELD_ROLES = [
    ("worker", "Буровые рабочие"),
    ("itr", "ИТР"),
    ("driver", "Водители"),
    ("cook", "Повар"),
]
OFFICE_ROLE = ("office", "Камеральная группа")

# Транспорт: (ключ, подпись, норма расхода, вид топлива).
VEHICLES = [
    ("uaz", "УАЗ", "fuel_uaz_l_per_100km", "ai92"),
    ("kamaz", "КАМАЗ", "fuel_kamaz_l_per_100km", "ai92"),
    ("zil", "ЗИЛ", "fuel_zil_l_per_100km", "ai92"),
    ("ural", "Урал", "fuel_ural_l_per_100km", "diesel"),
]
FUEL_LABELS = {"ai92": "АИ-92", "diesel": "дизель"}


@dataclass(frozen=True)
class CostInputs:
    """Ввод оператора для расчёта себестоимости. None — не задано."""

    brigades: int = 0
    itr: int = 0
    drivers: int = 0
    cooks: int = 0
    office_staff: int = 0
    office_months: float = 0.0
    # Оклады, руб./мес — без значений по умолчанию (список вопросов: сетка 2026).
    salary_worker: float | None = None
    salary_itr: float | None = None
    salary_driver: float | None = None
    salary_cook: float | None = None
    salary_office: float | None = None
    # Пробег техники за объект, км (туда-обратно и на участке).
    mileage_uaz: float = 0.0
    mileage_kamaz: float = 0.0
    mileage_zil: float = 0.0
    mileage_ural: float = 0.0
    # Цены топлива, руб./л — без значений по умолчанию.
    fuel_price_ai92: float | None = None
    fuel_price_diesel: float | None = None
    # Единиц техники на трале (доставка и вывоз); None — по числу бригад
    # (одна буровая установка на бригаду — ДОПУЩЕНИЕ).
    lowboy_units: int | None = None
    lab_samples: int = 0
    # Резерв непредвиденных, доля; None — норма (8,52 %, объект А, ФАКТ).
    contingency_rate: float | None = None
    depreciation: float = 0.0
    business_trip: float = 0.0

    @classmethod
    def from_dict(cls, data: dict | None) -> "CostInputs":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class CostResult(_CostResult):
    """Себестоимость геологии: общий результат (`core/cost.py`) + метраж."""

    @property
    def meters(self) -> float:
        return self.volume

    @property
    def cost_per_meter(self) -> float | None:
        return self.cost_per_unit


def _value(norms: dict[str, Norm], key: str) -> float:
    value = norms[key].value
    if value is None:
        raise ValueError(f"Норма {key!r} не задана (data/norms/{NORMS_NAME}.json)")
    return float(value)


# Ориентир доли ФОТ в себестоимости ИГИ. Первоисточник в репозитории не найден,
# только вторичный — поэтому ДОПУЩЕНИЕ: аудит 30.10.2026
# (внутренняя проверка, раздел 4.1 и итог п. 7: «ориентир
# объект А — 30–33 %»); первоисточник — структура затрат объекта А (2016 г.) в
# книге «Бюджет_контракта_v5.2» вне репозитория; аудитор сверял его со своим
# расчётом, а не с бухгалтерским фактом.
FOT_SHARE_REFERENCE = (0.30, 0.33)
FOT_SHARE_SOURCE = (
    "ДОПУЩЕНИЕ: ориентир по одному объекту 2016 г., с фактом бухгалтерии не сверен"
)
FIXED_WHAT = "проезд партии, камеральная группа, трал"

# Статьи, которые почти не зависят от метража: проезд партии, камеральная группа
# (численность × месяцы вводятся отдельно от бурения), доставка техники тралом.
FIXED_COST_LINES = ("travel", "fot_office", "lowboy")
# Суммы, которые оператор вводит на объект целиком (от метража не зависят).
LUMP_SUM_LINES = ("depreciation", "business_trip")
# Начисления долей: соцотчисления, накладные, налоги — от ФОТ; резерв — от суммы.
SHARE_LINES = ("social", "overhead", "taxes", "contingency")


def article_kind(key: str) -> str:
    """Тип статьи геологии по ключу (у строк расчёта он записан в `CostLine.kind`)."""
    if key in FIXED_COST_LINES or key in LUMP_SUM_LINES:
        return ARTICLE_FIXED
    if key in SHARE_LINES:
        return ARTICLE_SHARE
    return ARTICLE_VOLUME


def _line(key: str, article: str, amount: float | None, basis: str, **extra) -> CostLine:
    """Строка геологии: тип по ключу, постоянные затраты партии — `mobilization`."""
    return CostLine(key, article, amount, basis, kind=article_kind(key),
                    mobilization=key in FIXED_COST_LINES, **extra)


def compute_cost(
    inputs: CostInputs,
    meters: float,
    distance_km: float | None = None,
    norms: dict[str, Norm] | None = None,
    shared: SharedCosts | None = None,
) -> CostResult:
    """Себестоимость по вводу, суммарному метражу бурения (из утверждённой
    ведомости) и расстоянию до объекта (данные объекта; нужно для трала).
    `shared` — общие затраты объекта (`core/cost.py::SharedCosts`): строки
    «общие: …» перед накладными; без них (по умолчанию) — расчёт как до
    03.11.2026."""
    # по умолчанию — с общим слоем ставок (cost_common), как на странице «4. Минимальный бюджет»
    norms = norms if norms is not None else load_cost_norms(NORMS_NAME)
    missing: list[str] = []
    warnings: list[str] = []
    lines: list[CostLine] = []

    rate = _value(norms, "drilling_rate_pm_per_brigade_month")
    field_months: float | None = None
    if inputs.brigades > 0 and meters > 0:
        field_months = meters / (rate * inputs.brigades)
    elif meters > 0:
        missing.append("число буровых бригад")
    else:
        missing.append("метраж бурения (в утверждённой ведомости 0)")
    months = field_months or 0.0

    per_brigade = _value(norms, "workers_per_brigade")
    counts = {
        "worker": int(round(inputs.brigades * per_brigade)),
        "itr": inputs.itr,
        "driver": inputs.drivers,
        "cook": inputs.cooks,
    }
    salaries = {
        "worker": inputs.salary_worker,
        "itr": inputs.salary_itr,
        "driver": inputs.salary_driver,
        "cook": inputs.salary_cook,
        "office": inputs.salary_office,
    }
    headcount = sum(counts.values())

    # --- ФОТ: численность × оклад × месяцы, поле и камералка раздельно ---
    fot = 0.0
    for role, label in FIELD_ROLES:
        count, salary = counts[role], salaries[role]
        if count and salary is None:
            missing.append(f"оклад: {label.lower()}")
        amount = count * (salary or 0.0) * months
        fot += amount
        lines.append(_line(
            f"fot_{role}", f"ФОТ полевой: {label}",
            amount if not (count and salary is None) else None,
            f"{count} чел. × {salary if salary is not None else '—'} руб./мес × {months:.3f} мес",
            fot=True,
        ))
    office_count, office_salary = inputs.office_staff, salaries["office"]
    if office_count and inputs.office_months and office_salary is None:
        missing.append("оклад: камеральная группа")
    office_amount = office_count * (office_salary or 0.0) * inputs.office_months
    fot += office_amount
    lines.append(_line(
        "fot_office", f"ФОТ камеральный: {OFFICE_ROLE[1]}",
        office_amount if not (office_count and inputs.office_months and office_salary is None) else None,
        f"{office_count} чел. × {office_salary if office_salary is not None else '—'} руб./мес × "
        f"{inputs.office_months:g} мес",
        fot=True,
    ))

    social_rate = _value(norms, "social_contributions_rate")
    social = fot * social_rate
    lines.append(_line("social", "Соцотчисления", social, f"{social_rate:.1%} от ФОТ"))

    # --- содержание полевой партии ---
    for key, article in (
        ("field_allowance_per_person_month", "Полевое довольствие"),
        ("housing_per_person_month", "Жильё"),
        ("communication_per_person_month", "Связь"),
        ("household_per_person_month", "Бытовые расходы"),
    ):
        norm = _value(norms, key)
        lines.append(_line(key, article, headcount * months * norm,
                           f"{headcount} чел. × {months:.3f} мес × {norm:g} руб./чел.-мес"))
    travel = _value(norms, "travel_per_person")
    lines.append(_line("travel", "Проезд (туда-обратно)", headcount * travel,
                       f"{headcount} чел. × {travel:g} руб."))

    # --- топливо ---
    liters = {"ai92": 0.0, "diesel": 0.0}
    for key, _label, norm_key, fuel in VEHICLES:
        liters[fuel] += getattr(inputs, f"mileage_{key}") / 100 * _value(norms, norm_key)
    rig_hours = inputs.brigades * months * _value(norms, "field_hours_per_month")
    liters["diesel"] += rig_hours * _value(norms, "fuel_urb_l_per_hour")
    prices = {"ai92": inputs.fuel_price_ai92, "diesel": inputs.fuel_price_diesel}
    for fuel, volume in liters.items():
        price = prices[fuel]
        if volume and price is None:
            missing.append(f"цена топлива: {FUEL_LABELS[fuel]}")
        lines.append(_line(
            f"fuel_{fuel}", f"Топливо ({FUEL_LABELS[fuel]})",
            volume * (price or 0.0) if not (volume and price is None) else None,
            f"{volume:.1f} л × {price if price is not None else '—'} руб./л"
            + (f" (в т.ч. УРБ {rig_hours:.0f} ч)" if fuel == "diesel" and rig_hours else ""),
        ))

    # --- трал: доставка и вывоз техники ---
    units = inputs.lowboy_units if inputs.lowboy_units is not None else inputs.brigades
    lowboy_rate = _value(norms, "lowboy_per_km_one_way")
    if units and distance_km is None:
        missing.append("расстояние до объекта (данные объекта) — для трала")
    lowboy = 2 * (distance_km or 0.0) * lowboy_rate * units
    lines.append(_line(
        "lowboy", "Трал (доставка и вывоз техники)",
        lowboy if not (units and distance_km is None) else None,
        f"2 × {distance_km if distance_km is not None else '—'} км × {lowboy_rate:g} руб./км × {units} ед.",
    ))

    tools = _value(norms, "drilling_tools_per_pm") + _value(norms, "spare_parts_per_pm")
    lines.append(_line("tools", "Буровой инструмент и запчасти", meters * tools,
                       f"{meters:g} п.м. × {tools:g} руб./п.м."))
    lab = _value(norms, "external_lab_per_sample")
    lines.append(_line("lab", "Сторонние лабораторные", inputs.lab_samples * lab,
                       f"{inputs.lab_samples} обр. × {lab:g} руб."))
    lines.append(_line("depreciation", "Амортизация", inputs.depreciation, "ввод оператора"))
    lines.append(_line("business_trip", "Командировочные", inputs.business_trip, "ввод оператора"))
    if not inputs.depreciation and not inputs.business_trip:
        warnings.append(
            "Амортизация и командировочные = 0: в расчётной книге они исключены — спорно, проверьте."
        )
    lines += shared_lines(shared, field_months)

    direct, overhead, taxes, contingency, cost, contingency_rate = close_cost(
        lines, fot, norms, inputs.contingency_rate, NORMS_NAME)
    if inputs.lab_samples == 0:
        warnings.append("Сторонние лабораторные: число образцов 0 — проверьте.")
    warnings.append(
        "Все нормы расчёта — ДОПУЩЕНИЯ (источники — на странице «Справочники»), "
        "замените актуальными."
    )
    return CostResult(
        volume=meters, field_months=field_months, field_headcount=headcount,
        lines=tuple(lines), fot=fot, direct=direct, overhead=overhead, taxes=taxes,
        contingency=contingency, cost=cost, missing=tuple(missing), warnings=tuple(warnings),
        contingency_rate=contingency_rate, volume_unit="п.м.", section="geology",
        fixed_what=FIXED_WHAT, volume_word="метража",
        fot_reference=FOT_SHARE_REFERENCE, fot_reference_source=FOT_SHARE_SOURCE,
    )


# --- откуда ставки: статус калибровки минимума ---

# Ключевые ставки — те, что в шаблоне локального слоя
# (data/norms/norms_firm.example.json): оклады (поля ввода) и нормы на человека.
KEY_RATE_INPUTS = (
    ("salary_worker", "оклад бурового рабочего", "worker"),
    ("salary_itr", "оклад ИТР", "itr"),
    ("salary_driver", "оклад водителя", "driver"),
    ("salary_cook", "оклад повара", "cook"),
    ("salary_office", "оклад камеральщика", "office"),
)
KEY_RATE_NORMS = (
    ("field_allowance_per_person_month", "полевое довольствие"),
    ("housing_per_person_month", "жильё"),
    ("travel_per_person", "проезд"),
)


def rates_status(
    inputs: CostInputs, norms: dict[str, Norm], local_inputs: dict[str, LocalValue] | None = None,
) -> RatesStatus:
    """Откуда ключевые ставки (`KEY_RATE_INPUTS`, `KEY_RATE_NORMS`) — общая
    логика `core/cost.py::rates_status_for`. Оклад должности, которой в
    партии нет, не учитывается."""
    counts = {"worker": inputs.brigades, "itr": inputs.itr, "driver": inputs.drivers,
              "cook": inputs.cooks, "office": inputs.office_staff}
    roles = [(key, label, counts[role]) for key, label, role in KEY_RATE_INPUTS]
    return rates_status_for(inputs.to_dict(), roles, list(KEY_RATE_NORMS), norms, local_inputs)


# --- вариант А: контроль по архиву фактической себестоимости (Э7) ---

ARCHIVE_PATH = Path(__file__).resolve().parent.parent.parent / "reference_data" / "archive_unit_costs.json"
# Настоящий архив — данные компании, в публичную копию и демо-сборку не входит; там — демонстрационный
# файл с условными объектами (пометка в подписи источника).
DEMO_ARCHIVE_PATH = ARCHIVE_PATH.with_name("archive_unit_costs.demo.json")
ARCHIVE_CAPTION = "Несколько объектов разных лет — не статистика, контроль."


DEMO_ARCHIVE_NOTE = (
    "Демонстрационный архив: условные объекты и выдуманные числа, не фактическая себестоимость. "
    "Настоящий архив компании в эту сборку не входит."
)


def is_demo_archive(source: str) -> bool:
    """Архив демонстрационный (по подписи источника файла)."""
    return source.startswith("ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ")


def archive_path() -> Path:
    """Файл архива: настоящий, если он есть, иначе демонстрационный (может не быть и его)."""
    return ARCHIVE_PATH if ARCHIVE_PATH.exists() else DEMO_ARCHIVE_PATH


def archive_disclaimer(objects: list["ArchiveObject"], year: int) -> str:
    """Подпись к таблице архива рядом с расчётом: это не статистика."""
    years = [o.year for o in objects]
    span = f"{min(years)}–{max(years)}" if years else "—"
    return (f"Это не статистика: {len(objects)} объектов разных лет ({span}), цены не приведены "
            f"к {year} г. — только грубый контроль порядка величины.")


@dataclass(frozen=True)
class ArchiveObject:
    code: str
    years: str
    year: int
    meters: float | None
    fact_cost_per_m: float | None
    norm_cost_per_m: float | None
    ratio: float | None
    note: str | None


def load_archive(path: Path | None = None) -> tuple[list[ArchiveObject], str]:
    """Объекты архива и общий источник (`archive_path()`: настоящий архив или демонстрационный).
    Нет ни одного файла — FileNotFoundError (интерфейс показывает «архив не подключён»)."""
    raw = json.loads((path or archive_path()).read_text(encoding="utf-8"))
    known = {f.name for f in dataclasses.fields(ArchiveObject)}
    objects = [ArchiveObject(**{k: v for k, v in item.items() if k in known}) for item in raw["objects"]]
    return objects, raw.get("source", "")


def archive_by_proximity(objects: list[ArchiveObject], year: int) -> list[ArchiveObject]:
    """Объекты по близости года к году расчёта (ближайшие — первыми; при
    равенстве — более поздний)."""
    return sorted(objects, key=lambda o: (abs(o.year - year), -o.year))


def archive_summary(objects: list[ArchiveObject], year: int, count: int = 2) -> str:
    """Строка «по архиву ближайших по дате объектов себестоимость 1 п.м. …;
    отношение к нормативу …» — только по объектам с фактом."""
    with_fact = [o for o in archive_by_proximity(objects, year) if o.fact_cost_per_m is not None][:count]
    if not with_fact:
        return "По архиву: НЕТ ДАННЫХ о себестоимости 1 п.м."
    costs = "; ".join(
        f"{o.code} ({o.years}) — {o.fact_cost_per_m:,.2f} руб./п.м.".replace(",", " ")
        + (f" ({o.note})" if o.note else "")
        for o in with_fact
    )
    ratios = [o for o in with_fact if o.ratio is not None]
    ratio_text = (", ".join(f"{o.code}: {o.ratio:g}" for o in ratios) if ratios else "НЕТ ДАННЫХ")
    return (f"По архиву ближайших по дате объектов себестоимость 1 п.м.: {costs}; "
            f"отношение к нормативу: {ratio_text}.")
