"""Общий каркас себестоимости (минимального бюджета) всех разделов — трек 3
прогона «два дня», 03.11.2026. Расчёт смет по сборникам не трогает.

Что здесь (общее для всех пяти разделов), а что в `modules/<раздел>/cost.py`
(специфика раздела — драйверы: из какого объёма ведомости и по какой норме
выработки получается срок поля, состав партии, особые статьи):

- статьи себестоимости (`CostLine`, `CostResult`) и замыкание расчёта
  (`close_cost`: накладные и налоги от ФОТ, резерв от суммы) — так же, как
  считала геология с 25.10.2026 (её числа не изменились);
- коэффициент снижения (`Reduction`, `reduction`), объяснение «минимум >
  максимума» по статьям (`loss_explanation`), структура по статьям;
- откуда ставки — статус калибровки (`RatesStatus`, `rates_status_for`);
- нормы разделов с общим слоем (`load_cost_norms`, `cost_input_defaults`):
  общие ставки (страховые взносы, накладные, налоги, резерв, полевое
  довольствие, жильё, связь, быт, проезд, расход топлива, цены топлива,
  оклады по должностям) задаются ОДИН раз — `data/norms/cost_common.json` и
  раздел "cost_common" локального слоя `data/local/norms_firm.json`; у
  раздела — только его особые нормы (`data/norms/<раздел>_cost.json`);
- типовой расчёт «партия × оклады × месяцы» для разделов без бурения
  (`PartySpec`, `compute_party_cost`) — геодезия, геофизика, экология,
  гидрометеорология;
- общие затраты нескольких разделов и минимум объекта (`SharedCosts`,
  `object_minimum`, `object_reduction`).

ПРАВИЛО ОБЩИХ ЗАТРАТ (мобилизация, доставка техники и имущества до объекта,
база экспедиции). Они вводятся ОДИН раз на объект (страница «4. Минимальный
бюджет», блок «Общие затраты объекта») и входят в минимум КАЖДОГО раздела
отдельной строкой «общие: …» — так коэффициент снижения раздела честный,
если раздел выполняется один. В минимум ОБЪЕКТА каждая общая затрата
входит один раз: остаётся у раздела, где она наибольшая (база — у раздела с
самым долгим полем), у остальных разделов исключается вместе с резервом на
неё (`object_minimum`). Это правило 1 из «Структура бюджета контракта»
(§3.4: «закреплять за одним модулем») и практика Формы 2.2 компании («транспорт
ИГМИ учтён в ИГДИ»); правило 2 (распределять по ФОТ) и окончательный выбор —
вопрос к пользователю (docs/minimum_po_razdelam.md).
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core.norms import (
    KIND_ACCOUNTING,
    KIND_PRICE,
    ORIGIN_LOCAL,
    ORIGIN_SEED,
    LocalValue,
    Norm,
    NormsFormatError,
    _local_section,
    load_norms,
    local_input_defaults,
)

COMMON_NORMS = "cost_common"
# До 03.11.2026 (вечер) общие ставки локального слоя записывались в раздел
# геологии ("geology_cost") — для остальных разделов они читаются оттуда, если
# в "cost_common" их нет (`load_cost_norms`, `cost_input_defaults`).
LEGACY_NORMS = "geology_cost"

# --- статьи ---------------------------------------------------------------------

ARTICLE_FIXED = "постоянная"
ARTICLE_VOLUME = "от объёма"
ARTICLE_SHARE = "доля от других статей"


@dataclass(frozen=True)
class CostLine:
    """Статья себестоимости. `amount=None` — не посчитана (не хватает ввода).

    `kind` — тип для таблицы статей (постоянная / от объёма / доля);
    `fot` — часть ФОТ (фонда оплаты труда); `mobilization` — постоянная
    затрата партии, которую можно разделить между объектами экспедиции
    (проезд, камеральная группа, трал) — её показывает объяснение «минимум >
    максимума»; `shared` — вид общей затраты объекта (`SHARED_KINDS`) или ""."""

    key: str
    article: str
    amount: float | None
    basis: str
    direct: bool = True  # входит в прямые затраты
    kind: str = ARTICLE_VOLUME
    fot: bool = False
    mobilization: bool = False
    shared: str = ""


# Тексты по умолчанию для объяснения «минимум > максимума» у разделов без бурения.
FIXED_WHAT_PARTY = "проезд партии, камеральная группа"


@dataclass(frozen=True)
class CostResult:
    """Себестоимость раздела: статьи и итоги. `volume` — главный объём
    раздела из утверждённой ведомости (п.м., га, точки), `volume_unit` — его
    единица; `fixed_what` / `volume_word` — подписи объяснения «минимум >
    максимума»; `fot_reference` — ориентир доли ФОТ (None — ориентира нет)."""

    volume: float
    field_months: float | None
    field_headcount: int
    lines: tuple[CostLine, ...]
    fot: float
    direct: float
    overhead: float
    taxes: float
    contingency: float
    cost: float
    missing: tuple[str, ...]
    warnings: tuple[str, ...]
    contingency_rate: float = 0.0
    volume_unit: str = ""
    section: str = ""
    fixed_what: str = FIXED_WHAT_PARTY
    volume_word: str = "объёма работ"
    fot_reference: tuple[float, float] | None = None
    fot_reference_source: str = ""

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def cost_per_unit(self) -> float | None:
        return self.cost / self.volume if self.volume else None

    @property
    def fot_share(self) -> float | None:
        return self.fot / self.cost if self.cost else None


def norm_value(norms: Mapping[str, Norm], key: str, name: str = "") -> float:
    """Значение нормы; нет значения — ошибка с подсказкой, в каком файле."""
    norm = norms.get(key)
    if norm is None or norm.value is None:
        where = f"data/norms/{name}.json" if name else "data/norms/"
        raise ValueError(f"Норма {key!r} не задана ({where})")
    return float(norm.value)


def close_cost(
    lines: list[CostLine], fot: float, norms: Mapping[str, Norm],
    contingency_override: float | None = None, name: str = "",
) -> tuple[float, float, float, float, float, float]:
    """Замыкание расчёта: прямые = сумма прямых статей; накладные и налоги —
    от ФОТ; резерв — от (прямые + накладные + налоги). Дописывает в `lines`
    строки «Накладные», «Налоги», «Резерв непредвиденных» и возвращает
    (прямые, накладные, налоги, резерв, себестоимость, ставка резерва).
    Порядок действий — как в расчёте геологии с 25.10.2026 (её числа те же)."""
    direct = sum(line.amount or 0.0 for line in lines if line.direct)
    overhead_rate = norm_value(norms, "overhead_rate", name)
    taxes_rate = norm_value(norms, "taxes_rate", name)
    overhead, taxes = fot * overhead_rate, fot * taxes_rate
    lines.append(CostLine("overhead", "Накладные", overhead, f"{overhead_rate:.1%} от ФОТ",
                          direct=False, kind=ARTICLE_SHARE))
    lines.append(CostLine("taxes", "Налоги", taxes, f"{taxes_rate:.2%} от ФОТ",
                          direct=False, kind=ARTICLE_SHARE))
    # Резерв — доля: 8,52, введённое вместо 0,0852, давало себестоимость ×9 при «полном»
    # расчёте (аудит 05.11.2026). Вне 0…1 ввод не принимается: норма и причина в строке.
    bad_override = contingency_override is not None and not 0 <= contingency_override <= 1
    contingency_rate = (contingency_override if contingency_override is not None and not bad_override
                        else norm_value(norms, "contingency_rate", name))
    contingency = contingency_rate * (direct + overhead + taxes)
    how = f"{contingency_rate:.2%} от (прямые + накладные + налоги)"
    if bad_override:
        how += (f"; введённое {contingency_override:g} не принято — это должна быть доля от 0 до 1 "
                "(0,0852 = 8,52 %), взята норма")
    lines.append(CostLine("contingency", "Резерв непредвиденных", contingency, how,
                          direct=False, kind=ARTICLE_SHARE))
    cost = direct + overhead + taxes + contingency
    return direct, overhead, taxes, contingency, cost, contingency_rate


# --- три числа: максимум / минимум / коэффициент --------------------------------


@dataclass(frozen=True)
class Reduction:
    """Три числа для оператора: максимум (смета СБЦ), минимум
    (себестоимость), коэффициент снижения = минимум / максимум."""

    maximum: float | None
    minimum: float | None
    coefficient: float | None
    warnings: list[str] = field(default_factory=list)
    # Минимум сверен с фактом закрытого объекта? Пока нет ни одного такого
    # объекта с данными бухгалтерии (карта Э8а, аудит 30.10.2026 §4.4) —
    # коэффициент только ориентировочный; интерфейс показывает CALIBRATION_NOTE.
    calibrated: bool = False

    @property
    def possible(self) -> bool:
        """Снижение возможно: коэффициент посчитан и не больше 1. При минимуме
        выше максимума коэффициент не показывается как рекомендация."""
        return self.coefficient is not None and self.coefficient <= 1


REDUCTION_IMPOSSIBLE = "снижение невозможно"


def coefficient_text(red: Reduction) -> str:
    """Значение метрики «Коэффициент снижения»: число, «—» или «снижение невозможно»."""
    if red.coefficient is None:
        return "—"
    return f"{red.coefficient:.3f}".replace(".", ",") if red.possible else REDUCTION_IMPOSSIBLE


CALIBRATION_NOTE = (
    "Минимум не калиброван: расчёт ещё не сверен с фактической себестоимостью ни одного "
    "закрытого объекта (нужны данные бухгалтерии). Коэффициент снижения — ориентир, "
    "не основание для цены."
)


def money(value: float) -> str:
    """«1 234 567,89 руб.» — как `app/ui.py::rub` (ядро не зависит от интерфейса)."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " руб."


def percent(share: float, digits: int = 0) -> str:
    """Доля → «29,3 %» (запятая, пробел перед знаком)."""
    return f"{share * 100:.{digits}f}".replace(".", ",") + " %"


# --- структура по статьям и объяснение «минимум > максимума» -----------------------


@dataclass(frozen=True)
class ArticleShare:
    """Статья себестоимости: сумма, доля в итоге и тип (постоянная / от объёма /
    доля от других статей)."""

    key: str
    article: str
    amount: float
    share: float
    kind: str


def cost_structure(cost: CostResult) -> list[ArticleShare]:
    """Статьи себестоимости с долями (сумма статей = себестоимость). Не
    посчитанные статьи (неполный ввод) — с суммой 0."""
    total = cost.cost
    return [
        ArticleShare(line.key, line.article, line.amount or 0.0,
                     (line.amount or 0.0) / total if total else 0.0, line.kind)
        for line in cost.lines
    ]


def top_articles(cost: CostResult, count: int = 3) -> list[ArticleShare]:
    """Статьи, которые больше всего «тянут» себестоимость (по сумме)."""
    return sorted(cost_structure(cost), key=lambda a: -a.amount)[:count]


def fixed_costs(cost: CostResult) -> float:
    """Постоянные затраты партии, которые можно разделить между объектами
    экспедиции (`CostLine.mobilization`), — ФОТ среди них с соцотчислениями."""
    amounts = {line.key: line.amount or 0.0 for line in cost.lines}
    social_rate = amounts.get("social", 0.0) / cost.fot if cost.fot else 0.0
    return sum((line.amount or 0.0) * (1 + social_rate if line.fot else 1)
               for line in cost.lines if line.mobilization)


def fot_share_text(cost: CostResult) -> str | None:
    """«Доля ФОТ X % против ориентира …» с выводом; без ориентира — только
    доля и что ориентира нет; None — ФОТ нет."""
    share = cost.fot_share
    if not share:
        return None
    if cost.fot_reference is None:
        return (f"Доля ФОТ (фонда оплаты труда) — {percent(share, 1)} себестоимости; ориентира "
                "доли для этого раздела нет (структура фактических затрат известна только по "
                "геологии) — сравнить не с чем.")
    low, high = cost.fot_reference
    if share < low:
        verdict = ("ниже ориентира: остальные статьи (жильё, проезд, топливо, трал) весят больше, "
                   "чем в фактической структуре затрат, — проверьте их ставки")
    elif share > high:
        verdict = ("выше ориентира: оклады, вероятно, взяты из цены (Форма 2.2), а не из штатного "
                   "расписания, — проверьте их")
    else:
        verdict = "в пределах ориентира"
    return (f"Доля ФОТ (фонда оплаты труда) — {percent(share, 1)} себестоимости, ориентир "
            f"{low * 100:.0f}–{high * 100:.0f} % ({cost.fot_reference_source}): {verdict}.")


def loss_explanation(cost: CostResult, smeta_max: float) -> list[str]:
    """Почему минимум (себестоимость) выше максимума (сметы по СБЦ): какие
    статьи тянут, доля ФОТ, постоянные затраты и что проверить. Пусто, если
    минимум не выше максимума."""
    if not smeta_max or cost.cost <= smeta_max:
        return []
    top = "; ".join(f"{a.article.lower()} — {money(a.amount)} ({percent(a.share)}, {a.kind})"
                    for a in top_articles(cost))
    texts = [f"Больше всего себестоимость тянут статьи: {top}."]
    fot_text = fot_share_text(cost)
    if fot_text:
        texts.append(fot_text)
    fixed = fixed_costs(cost)
    texts.append(
        f"Постоянные затраты ({cost.fixed_what}) — {money(fixed)}, "
        f"это {fixed / smeta_max:.0%} сметной стоимости. Они не зависят от {cost.volume_word}, "
        "поэтому на малом объекте минимум может оказаться выше максимума. Если объект "
        "выполняется одной экспедицией вместе с другими (как карьеры при дороге), разделите "
        "эти затраты между объектами: уменьшите численность камеральной группы, проезд"
        + (" и трал" if "трал" in cost.fixed_what else "") + " в этом расчёте."
    )
    shared = sum(line.amount or 0.0 for line in cost.lines if line.shared)
    if shared:
        texts.append(
            f"Общие затраты объекта (мобилизация, доставка, база) — {money(shared)} — в минимуме "
            "раздела учтены целиком, как если бы раздел выполнялся один; в минимум объекта они "
            "входят один раз."
        )
    texts.append(
        "Что сделать: проверьте ставки — вероятно, это цены (например, «Бюджетный расчёт», "
        "Форма 2.2), а не себестоимость; для малого объекта мобилизация ("
        + ("проезд, трал, камеральная группа" if "трал" in cost.fixed_what
           else "проезд, камеральная группа")
        + ") делится между объектами экспедиции. Коэффициент больше 1 — не "
        "рекомендация, а признак, что ввод нужно проверить."
    )
    return texts


def reduction(cost: CostResult, smeta_max: float | None) -> Reduction:
    """Максимум / минимум / коэффициент снижения раздела с предупреждениями."""
    warnings = []
    if smeta_max is None:
        warnings.append("Сметная стоимость по СБЦ на дату расчёта не посчитана (нет индекса) — "
                        "коэффициент снижения недоступен.")
        return Reduction(None, cost.cost if cost.complete else None, None, warnings)
    if not cost.complete:
        warnings.append("Расчёт себестоимости неполный (не заполнено: " + ", ".join(cost.missing)
                        + ") — коэффициент снижения не считается.")
        return Reduction(smeta_max, None, None, warnings)
    if smeta_max <= 0:
        warnings.append("Сметная стоимость по СБЦ равна 0 (цены не найдены?) — коэффициент снижения "
                        "не считается.")
        return Reduction(smeta_max, cost.cost, None, warnings)
    coefficient = cost.cost / smeta_max
    if cost.cost > smeta_max:
        warnings.append(
            f"Себестоимость ({money(cost.cost)}) выше сметной стоимости по СБЦ ({money(smeta_max)}): "
            "работа по смете убыточна — снижение невозможно."
        )
        warnings.extend(loss_explanation(cost, smeta_max))
    return Reduction(smeta_max, cost.cost, coefficient, warnings)


# --- откуда ставки: статус калибровки --------------------------------------------

RATE_ACCOUNTING = "accounting"  # локальный слой, kind="accounting" — от бухгалтерии
RATE_PRICE = "price"            # локальный слой, kind="price" — цена (Форма 2.2)
RATE_LOCAL = "local"            # локальный слой без отметки — происхождение не указано
RATE_SEED = "seed"              # допущение data/norms/*.json
RATE_OPERATOR = "operator"      # ввод на странице (отличается от локального слоя)
RATE_MISSING = "missing"        # не задана

RATE_ORIGIN_LABELS = {
    RATE_PRICE: "цены «Бюджетного расчёта» (Форма 2.2)",
    RATE_LOCAL: "локальный слой без отметки «от бухгалтерии»",
    RATE_SEED: "допущения",
    RATE_OPERATOR: "ввод на странице",
    RATE_MISSING: "не заданы",
}


@dataclass(frozen=True)
class RateOrigin:
    key: str
    label: str
    origin: str
    source: str = ""
    date: str = ""


def local_origin(kind: str) -> str:
    return {KIND_ACCOUNTING: RATE_ACCOUNTING, KIND_PRICE: RATE_PRICE}.get(kind, RATE_LOCAL)


@dataclass(frozen=True)
class RatesStatus:
    """Откуда ключевые ставки расчёта себестоимости. Калибровка по факту
    закрытого объекта — отдельный вопрос (`Reduction.calibrated`)."""

    rates: tuple[RateOrigin, ...]

    @property
    def from_accounting(self) -> bool:
        """Все ключевые ставки — от бухгалтерии (локальный слой, kind="accounting")."""
        return bool(self.rates) and all(r.origin == RATE_ACCOUNTING for r in self.rates)

    def text(self) -> str:
        if self.from_accounting:
            sources = list(dict.fromkeys(r.source for r in self.rates if r.source))
            dates = sorted({r.date for r in self.rates if r.date})
            shown = "; ".join(sources[:3]) + (f" и ещё {len(sources) - 3}" if len(sources) > 3 else "")
            return (
                f"Ставки компании от бухгалтерии (источник: {shown or 'не указан'}; дата: "
                f"{', '.join(dates) or 'не указана'}). Минимум всё равно не калиброван по факту "
                "закрытого объекта: расчёт ещё не сверен с фактической себестоимостью — "
                "коэффициент снижения ориентировочный."
            )
        groups: dict[str, list[str]] = {}
        for rate in self.rates:
            if rate.origin != RATE_ACCOUNTING:
                groups.setdefault(rate.origin, []).append(rate.label)
        details = "; ".join(f"{RATE_ORIGIN_LABELS[o]}: {', '.join(labels)}" for o, labels in groups.items())
        return (
            "Минимум не калиброван: ставки — допущения / цены, не себестоимость"
            + (f" ({details})" if details else "")
            + ". Себестоимость по таким ставкам может оказаться даже выше сметы. Нужны ставки "
            "бухгалтерии (шаблон ставок) и сверка с фактом "
            "закрытого объекта. Коэффициент снижения — ориентир, не основание для цены."
        )


def rates_status_for(
    values: Mapping[str, Any],
    salary_roles: list[tuple[str, str, int]],
    norm_keys: list[tuple[str, str]],
    norms: Mapping[str, Norm],
    local_inputs: Mapping[str, LocalValue] | None = None,
) -> RatesStatus:
    """Откуда ключевые ставки: оклады должностей, которые есть в партии
    (`salary_roles` — (поле оклада, подпись, численность)), и нормы на
    человека (`norm_keys`). Оклад из поля ввода считается ставкой локального
    слоя, только если совпадает с ней (иначе — ввод оператора). Признак «от
    бухгалтерии» — поле "kind": "accounting" у записи локального слоя
    (`core/norms.py`; ставит импорт шаблона бухгалтерии), дата — поле "date"."""
    local_inputs = local_inputs or {}
    rates = []
    for key, label, count in salary_roles:
        if not count:
            continue
        value, local = values.get(key), local_inputs.get(key)
        if value is None:
            rates.append(RateOrigin(key, label, RATE_MISSING))
        elif local is not None and abs(float(value) - local.value) < 0.005:
            rates.append(RateOrigin(key, label, local_origin(local.kind), local.source, local.date))
        else:
            rates.append(RateOrigin(key, label, RATE_OPERATOR))
    for key, label in norm_keys:
        norm = norms.get(key)
        if norm is None or norm.value is None:
            rates.append(RateOrigin(key, label, RATE_MISSING))
        elif norm.origin == ORIGIN_SEED:
            rates.append(RateOrigin(key, label, RATE_SEED, norm.source))
        else:
            rates.append(RateOrigin(key, label, local_origin(norm.kind),
                                    norm.source.removeprefix("локально: "), norm.date))
    return RatesStatus(tuple(rates))


# --- нормы с общим слоем -----------------------------------------------------------

# Поля ввода геологии, которые по смыслу совпадают с общими должностями: если в
# локальном слое геологии своего значения нет, по умолчанию берётся общее.
INPUT_ALIASES = {
    LEGACY_NORMS: {"salary_driver": "salary_field_driver", "salary_office": "salary_office_engineer"},
}


def _local_layer(name: str, base: Mapping[str, Norm], local_dir) -> dict[str, Norm]:
    """Записи раздела `name` локального слоя, применённые к нормам `base`
    (только перекрытые ключи; правила — как у `core.norms.load_norms`: пустое
    значение пропускается, запись без источника — ошибка). Раздел может
    перекрыть и общую норму, которой нет в его seed-файле."""
    result = {}
    for key, item in _local_section(name, local_dir).get("norms", {}).items():
        if key not in base or item.get("value") is None:
            continue
        if not item.get("source"):
            raise NormsFormatError(f"локальный слой: у нормы {key!r} нет источника")
        result[key] = dataclasses.replace(
            base[key], value=item["value"], source=f"локально: {item['source']}",
            assumption=bool(item.get("assumption", False)), note=item.get("note", ""),
            origin=ORIGIN_LOCAL, kind=str(item.get("kind") or ""), date=str(item.get("date") or ""),
        )
    return result


def load_cost_norms(name: str, local_dir=None, use_local: bool = True) -> dict[str, Norm]:
    """Нормы раздела `name` вместе с общими (`data/norms/cost_common.json`).

    Старшинство (сверху вниз): локальный слой раздела → локальный слой общих
    ставок ("cost_common") → для разделов кроме геологии — общие ставки,
    записанные до 03.11.2026 в раздел геологии локального слоя → seed раздела
    → seed общих. Итог: ответ бухгалтерии, импортированный один раз в общие
    ставки, закрывает все разделы; у раздела перекрываются только его особые
    нормы. Битый локальный слой — исключение (`NormsFormatError` / OSError):
    вызывающий показывает предупреждение и работает на seed (`use_local=False`)."""
    common_seed = load_norms(COMMON_NORMS, use_local=False)
    merged = dict(common_seed)
    if name != COMMON_NORMS:
        merged.update(load_norms(name, use_local=False))
    if not use_local:
        return merged
    layers = [_local_layer(name, merged, local_dir) if name != COMMON_NORMS else {},
              _local_layer(COMMON_NORMS, merged, local_dir)]
    if name not in (LEGACY_NORMS, COMMON_NORMS):
        legacy = _local_layer(LEGACY_NORMS, merged, local_dir)
        layers.append({k: n for k, n in legacy.items() if k in common_seed})
    for key in merged:
        for layer in layers:
            norm = layer.get(key)
            if norm is not None and norm.origin == ORIGIN_LOCAL:
                merged[key] = norm
                break
    return merged


def cost_input_defaults(name: str, local_dir=None) -> dict[str, LocalValue]:
    """Значения полей ввода по умолчанию из локального слоя: общие ("cost_common":
    оклады по должностям, цены топлива) + особые раздела (раздел перекрывает).
    Для геологии её поля «водитель» и «камеральщик» по умолчанию берутся из общих
    должностей, если своего значения нет (`INPUT_ALIASES`). Для остальных
    разделов цены топлива, записанные до 03.11.2026 в раздел геологии, — запасной
    источник общих полей."""
    common = local_input_defaults(COMMON_NORMS, local_dir=local_dir)
    result: dict[str, LocalValue] = {}
    if name not in (LEGACY_NORMS, COMMON_NORMS):
        legacy = local_input_defaults(LEGACY_NORMS, local_dir=local_dir)
        result.update({k: v for k, v in legacy.items() if k in COMMON_INPUT_KEYS})
    result.update(common)
    for own, common_key in INPUT_ALIASES.get(name, {}).items():
        if common_key in common:
            result[own] = common[common_key]
    if name != COMMON_NORMS:
        result.update(local_input_defaults(name, local_dir=local_dir))
    return result


# --- поля ввода --------------------------------------------------------------------

KIND_INT = "int"            # целое ≥ 0, по умолчанию 0
KIND_FLOAT = "float"        # дробное ≥ 0, по умолчанию 0
KIND_OPTIONAL = "optional"  # дробное без значения по умолчанию (пусто = не задано)


@dataclass(frozen=True)
class InputField:
    name: str
    label: str
    kind: str
    help: str = ""


@dataclass(frozen=True)
class Role:
    """Должность партии: поле численности, подпись, поле оклада."""

    key: str
    label: str
    salary_key: str
    office: bool = False


FIELD_ROLES = (
    Role("field_lead", "Ведущий инженер (поле)", "salary_field_lead"),
    Role("field_engineer", "Инженеры (поле)", "salary_field_engineer"),
    Role("field_driver", "Водители", "salary_field_driver"),
    Role("field_worker", "Рабочие", "salary_field_worker"),
)
OFFICE_ROLES = (
    Role("office_chief", "Главный специалист (камералка)", "salary_office_chief", True),
    Role("office_lead", "Руководитель группы (камералка)", "salary_office_lead", True),
    Role("office_engineer", "Инженеры (камералка)", "salary_office_engineer", True),
    Role("office_technician", "Техники (камералка)", "salary_office_technician", True),
)
ROLES = FIELD_ROLES + OFFICE_ROLES

SALARY_HELP = ("Начисленная зарплата одного человека за месяц с районным коэффициентом и северной "
               "надбавкой, без страховых взносов.")
# Общие поля (один раз на все разделы без бурения): оклады по должностям и цены топлива.
COMMON_FIELDS = tuple(
    InputField(role.salary_key, f"Оклад: {role.label.lower()}, руб./мес", KIND_OPTIONAL, SALARY_HELP)
    for role in ROLES
) + (
    InputField("fuel_price_ai92", "Цена АИ-92, руб./л", KIND_OPTIONAL),
    InputField("fuel_price_diesel", "Цена дизеля, руб./л", KIND_OPTIONAL),
)
COMMON_INPUT_KEYS = frozenset(f.name for f in COMMON_FIELDS)

# Техника своих разделов: (ключ, подпись, норма расхода общих норм, топливо).
VEHICLES = (
    ("uaz", "УАЗ", "fuel_uaz_l_per_100km", "ai92"),
    ("kamaz", "КАМАЗ", "fuel_kamaz_l_per_100km", "ai92"),
    ("zil", "ЗИЛ", "fuel_zil_l_per_100km", "ai92"),
    ("ural", "Урал", "fuel_ural_l_per_100km", "diesel"),
)
FUEL_LABELS = {"ai92": "АИ-92", "diesel": "дизель"}

# Поля раздела (типовой расчёт партии), особые поля раздела — `PartySpec.extra_fields`.
PARTY_FIELDS = tuple(
    InputField(role.key, f"{role.label}, чел.", KIND_INT) for role in ROLES
) + (
    InputField("office_months", "Камеральные работы, мес", KIND_FLOAT),
    InputField("field_months", "Срок полевых работ, мес (пусто — по норме выработки)", KIND_OPTIONAL,
               "Если задан — программа берёт его, а не срок по норме выработки."),
) + tuple(
    InputField(f"mileage_{key}", f"Пробег {label}, км", KIND_FLOAT,
               "За объект целиком: дорога до объекта и обратно + работа на участке.")
    for key, label, _norm, _fuel in VEHICLES
) + (
    InputField("materials", "Материалы и снаряжение, руб.", KIND_FLOAT,
               "Канцелярия, бытовые, медицинские принадлежности, охрана труда — сумма на объект."),
    InputField("depreciation", "Амортизация, руб.", KIND_FLOAT),
    InputField("business_trip", "Командировочные, руб.", KIND_FLOAT),
    InputField("contingency_rate", "Резерв непредвиденных, доля (пусто — норма)", KIND_OPTIONAL,
               "Доля от 0 до 1: 0,0852 = 8,52 %. Значение вне этих пределов не принимается — берётся норма."),
)


def widget_value(kind: str, value: Any) -> Any:
    """Значение поля по виду: целое / дробное (0 по умолчанию) / необязательное."""
    if kind == KIND_INT:
        return int(value or 0)
    if kind == KIND_FLOAT:
        return float(value or 0.0)
    return None if value is None else float(value)


# --- общие затраты объекта ---------------------------------------------------------

# Вид общей затраты → подпись строки в минимуме раздела.
SHARED_KINDS = {
    "mobilization": "Общие: организация и ликвидация работ (мобилизация)",
    "delivery": "Общие: доставка техники и имущества до объекта",
    "base": "Общие: база экспедиции",
}


@dataclass(frozen=True)
class SharedCosts:
    """Общие затраты нескольких разделов, ввод один раз на объект. Норм нет —
    суммы вводит оператор (0 — затраты нет)."""

    mobilization: float = 0.0
    delivery: float = 0.0
    base_per_month: float = 0.0

    @classmethod
    def from_dict(cls, data: Mapping | None) -> "SharedCosts":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: float(v or 0.0) for k, v in (data or {}).items() if k in known})

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def empty(self) -> bool:
        return not (self.mobilization or self.delivery or self.base_per_month)


def shared_lines(shared: SharedCosts | None, field_months: float | None) -> list[CostLine]:
    """Строки общих затрат в минимуме раздела (только ненулевые)."""
    if shared is None:
        return []
    lines = []
    if shared.mobilization:
        lines.append(CostLine("shared_mobilization", SHARED_KINDS["mobilization"], shared.mobilization,
                              "сумма на объект (ввод), в минимум объекта — один раз",
                              kind=ARTICLE_FIXED, shared="mobilization"))
    if shared.delivery:
        lines.append(CostLine("shared_delivery", SHARED_KINDS["delivery"], shared.delivery,
                              "сумма на объект (ввод), в минимум объекта — один раз",
                              kind=ARTICLE_FIXED, shared="delivery"))
    if shared.base_per_month:
        months = field_months or 0.0
        lines.append(CostLine("shared_base", SHARED_KINDS["base"], shared.base_per_month * months,
                              f"{shared.base_per_month:g} руб./мес × {months:.3f} мес поля; в минимум "
                              "объекта — один раз, по самому долгому полю",
                              kind=ARTICLE_FIXED, shared="base"))
    return lines


@dataclass(frozen=True)
class SectionShare:
    code: str
    label: str
    cost: float  # минимум раздела целиком (с общими затратами)


@dataclass(frozen=True)
class SharedOnce:
    kind: str
    label: str
    amount: float  # входит в минимум объекта один раз
    section: str   # у какого раздела оставлена
    excluded: float  # исключено у остальных разделов (с резервом на них)


@dataclass(frozen=True)
class ObjectMinimum:
    """Минимум объекта: сумма минимумов разделов без повторов общих затрат."""

    total: float | None
    sections: tuple[SectionShare, ...]
    incomplete: tuple[tuple[str, tuple[str, ...]], ...]
    shared: tuple[SharedOnce, ...]
    from_accounting: bool
    calibrated: bool = False  # факта закрытого объекта нет ни по одному разделу

    @property
    def complete(self) -> bool:
        return bool(self.sections) and not self.incomplete

    @property
    def duplicates_excluded(self) -> float:
        return sum(s.excluded for s in self.shared)

    def text(self) -> str:
        """Короткое описание для интерфейса и предупреждения «цена ниже минимума»."""
        if self.total is None:
            return "Минимум объекта не посчитан: нет ни одного раздела с полным расчётом себестоимости."
        parts = [f"Минимум объекта (себестоимость без НДС) — {money(self.total)} по разделам: "
                 + ", ".join(s.label for s in self.sections) + "."]
        if self.shared:
            parts.append("Общие затраты — один раз: " + "; ".join(
                f"{s.label.removeprefix('Общие: ')} — {money(s.amount)} (у раздела «{s.section}»)"
                for s in self.shared) + ".")
        if self.incomplete:
            parts.append("Не вошли (расчёт неполный): " + ", ".join(label for label, _m in self.incomplete) + ".")
        parts.append("Ставки от бухгалтерии." if self.from_accounting else
                     "Не калиброван: ставки — допущения / цены.")
        return " ".join(parts)


def object_minimum(
    results: Mapping[str, CostResult],
    labels: Mapping[str, str] | None = None,
    statuses: Mapping[str, RatesStatus] | None = None,
) -> ObjectMinimum:
    """Минимум объекта по себестоимостям разделов (`results`: код → расчёт).

    Полные расчёты складываются; неполные не входят и перечисляются. Общие
    затраты (строки с `CostLine.shared`) входят один раз: у каждого вида
    остаётся строка раздела с наибольшей суммой (база — по самому долгому
    полю), у остальных разделов она исключается вместе с резервом
    непредвиденных на неё (резерв считается от прямых затрат, куда общая
    затрата входит). `from_accounting` — у всех вошедших разделов ключевые
    ставки от бухгалтерии (`statuses`; нет статуса — не от бухгалтерии)."""
    labels = labels or {}
    statuses = statuses or {}
    complete = {code: r for code, r in results.items() if r.complete}
    incomplete = tuple((labels.get(code, code), r.missing) for code, r in results.items()
                       if not r.complete)
    if not complete:
        return ObjectMinimum(None, (), incomplete, (), False)
    total = sum(r.cost for r in complete.values())
    shared = []
    for kind, kind_label in SHARED_KINDS.items():
        entries = [(code, line.amount or 0.0) for code, r in complete.items()
                   for line in r.lines if line.shared == kind and line.amount]
        if not entries:
            continue
        keep_code, keep_amount = max(entries, key=lambda e: e[1])
        excluded = 0.0
        skipped_keep = False
        for code, amount in entries:
            if code == keep_code and amount == keep_amount and not skipped_keep:
                skipped_keep = True
                continue
            excluded += amount * (1 + complete[code].contingency_rate)
        total -= excluded
        shared.append(SharedOnce(kind, kind_label, keep_amount, labels.get(keep_code, keep_code), excluded))
    sections = tuple(SectionShare(code, labels.get(code, code), r.cost) for code, r in complete.items())
    from_accounting = all(statuses.get(code) is not None and statuses[code].from_accounting
                          for code in complete)
    return ObjectMinimum(total, sections, incomplete, tuple(shared), from_accounting)


def object_reduction(minimum: ObjectMinimum, maxima: Mapping[str, float | None]) -> Reduction:
    """Коэффициент снижения по объекту: минимум объекта / сумма максимумов
    (смет по СБЦ без НДС) тех же разделов. Раздел с минимумом без сметы —
    коэффициент не считается (сравнивать было бы не с чем)."""
    warnings = []
    if minimum.total is None:
        return Reduction(None, None, None, [minimum.text()])
    codes = [s.code for s in minimum.sections]
    without = [s.label for s in minimum.sections if maxima.get(s.code) is None]
    if without:
        warnings.append("Нет сметы по СБЦ на дату расчёта у разделов: " + ", ".join(without)
                        + " — коэффициент по объекту не считается.")
        return Reduction(None, minimum.total, None, warnings)
    maximum = sum(float(maxima[c]) for c in codes)
    if maximum <= 0:
        return Reduction(maximum, minimum.total, None, ["Сумма смет по СБЦ равна 0."])
    if minimum.incomplete:
        warnings.append("В минимум объекта не вошли разделы с неполным расчётом: "
                        + ", ".join(label for label, _m in minimum.incomplete)
                        + " — коэффициент только по вошедшим.")
    coefficient = minimum.total / maximum
    if coefficient > 1:
        warnings.append(f"Минимум объекта ({money(minimum.total)}) выше суммы смет по СБЦ "
                        f"({money(maximum)}) — снижение невозможно.")
    return Reduction(maximum, minimum.total, coefficient, warnings)


# --- типовой расчёт партии (разделы без бурения) ---------------------------------


def excel_round_half_up(value: float) -> int:
    """ROUND(x; 0) Excel для x ≥ 0 (половина — вверх; Python round — к чётному)."""
    return int(math.floor(value + 0.5))


@dataclass(frozen=True)
class PartySpec:
    """Описание раздела для типового расчёта «партия × оклады × месяцы».

    `rate_norm_key` — норма выработки на одного человека полевой группы за
    месяц (объём / (норма × численность) = срок поля); None — нормы нет, срок
    поля вводит оператор. `suggest(n)` — состав партии из n человек поля по
    правилу (численность по должностям, вкл. камеральных). `min_party` и
    `fixed_party` — правило числа людей поля (v5.2: не менее 2; у ИГМИ —
    фиксированный состав). `extra_lines(values, norms, context)` — особые
    статьи раздела: (строки, не заполнено, предупреждения)."""

    code: str
    label: str
    norms_name: str
    volume_label: str
    volume_unit: str
    rate_norm_key: str | None
    suggest: Callable[[int], dict[str, int]]
    rule_source: str
    min_party: int = 2
    fixed_party_norm_key: str | None = None
    extra_fields: tuple[InputField, ...] = ()
    extra_lines: Callable[[Mapping, Mapping[str, Norm], Mapping], tuple[list, list, list]] | None = None
    fixed_what: str = FIXED_WHAT_PARTY
    volume_word: str = "объёма работ"
    archive_note: str = ("Контроль по архиву невозможен: пар «фактическая себестоимость / норматив» "
                         "по этому разделу нет ни в архиве, ни в найденных файлах.")

    @property
    def fields(self) -> tuple[InputField, ...]:
        return PARTY_FIELDS + self.extra_fields


def party_size(spec: PartySpec, volume: float, months: float, norms: Mapping[str, Norm]) -> int:
    """Сколько человек поля нужно по правилу раздела при сроке `months`:
    объём / (норма × срок), вверх, не меньше `min_party` (v5.2 ПР1_ТРУД F6,
    F10, F11); у раздела с фиксированным составом — норма состава."""
    if spec.fixed_party_norm_key:
        return int(norm_value(norms, spec.fixed_party_norm_key, spec.norms_name))
    if not spec.rate_norm_key or volume <= 0:
        return 0
    rate = norm_value(norms, spec.rate_norm_key, spec.norms_name)
    minimum = norms.get("min_field_party")
    least = int(minimum.value) if minimum is not None and minimum.value is not None else spec.min_party
    return max(least, math.ceil(volume / (rate * max(months, 0.01)) - 1e-9))


def suggested_party(spec: PartySpec, volume: float, months: float,
                    norms: Mapping[str, Norm]) -> dict[str, int]:
    """Состав партии по правилу раздела (численность по должностям)."""
    people = party_size(spec, volume, months, norms)
    return spec.suggest(people) if people else {}


KEY_NORMS_PARTY = [
    ("field_allowance_per_person_month", "полевое довольствие"),
    ("housing_per_person_month", "жильё"),
    ("travel_per_person", "проезд"),
]


def party_rates_status(spec: PartySpec, values: Mapping, norms: Mapping[str, Norm],
                       local_inputs: Mapping[str, LocalValue] | None = None) -> RatesStatus:
    """Статус калибровки типового расчёта: оклады должностей, которые есть в
    партии, и нормы на человека."""
    office_months = float(values.get("office_months") or 0)
    roles = [(role.salary_key, f"оклад: {role.label.lower()}",
              int(values.get(role.key) or 0) if (not role.office or office_months) else 0)
             for role in ROLES]
    return rates_status_for(values, roles, KEY_NORMS_PARTY, norms, local_inputs)


def compute_party_cost(
    spec: PartySpec,
    values: Mapping[str, Any],
    volume: float,
    norms: Mapping[str, Norm] | None = None,
    shared: SharedCosts | None = None,
    context: Mapping | None = None,
) -> CostResult:
    """Себестоимость раздела типовым расчётом: срок поля = объём / (норма ×
    численность поля) или ввод; ФОТ = численность × оклад × месяцы (поле и
    камералка раздельно); соцотчисления; содержание партии на человека в
    месяц (полевое довольствие, жильё, связь, быт); проезд; топливо по пробегу;
    особые статьи раздела; суммы на объект; общие затраты объекта; накладные,
    налоги, резерв (`close_cost`). Не хватает ввода — статья без суммы и
    причина в `missing` (коэффициент снижения тогда не считается)."""
    norms = norms if norms is not None else load_cost_norms(spec.norms_name)
    name = spec.norms_name
    missing: list[str] = []
    warnings: list[str] = []
    lines: list[CostLine] = []
    count = {role.key: int(values.get(role.key) or 0) for role in ROLES}
    headcount = sum(count[role.key] for role in FIELD_ROLES)
    office_months = float(values.get("office_months") or 0.0)

    override = values.get("field_months")
    rate = (norm_value(norms, spec.rate_norm_key, name)
            if spec.rate_norm_key and norms.get(spec.rate_norm_key) is not None
            and norms[spec.rate_norm_key].value is not None else None)
    field_months: float | None = None
    if override is not None and float(override) > 0:
        field_months = float(override)
        if headcount == 0:
            missing.append("численность полевой группы")
        if rate and volume > 0 and headcount:
            need = volume / rate
            if headcount * field_months < 0.8 * need:
                warnings.append(
                    f"Состав и срок поля ({headcount} чел. × {field_months:g} мес = "
                    f"{headcount * field_months:g} чел.-мес) меньше нормы выработки: на "
                    f"{volume:g} {spec.volume_unit} нужно {need:.1f} чел.-мес — проверьте."
                )
    elif rate is None:
        missing.append("срок полевых работ (нормы выработки нет — вводит оператор)")
    elif volume <= 0:
        missing.append(f"{spec.volume_label} (в утверждённой ведомости 0) или срок полевых работ")
    elif headcount == 0:
        missing.append("численность полевой группы")
    else:
        field_months = volume / (rate * headcount)
    months = field_months or 0.0

    # --- ФОТ ---
    fot = 0.0
    for role in ROLES:
        n = count[role.key]
        if not n:
            continue
        role_months = office_months if role.office else months
        salary = values.get(role.salary_key)
        lacks = salary is None and (role_months or not role.office)
        if lacks:
            missing.append(f"оклад: {role.label.lower()}")
        amount = n * float(salary or 0.0) * role_months
        fot += amount
        lines.append(CostLine(
            f"fot_{role.key}",
            f"ФОТ {'камеральный' if role.office else 'полевой'}: {role.label}",
            None if lacks else amount,
            f"{n} чел. × {salary if salary is not None else '—'} руб./мес × {role_months:.3f} мес",
            fot=True, kind=ARTICLE_FIXED if role.office else ARTICLE_VOLUME, mobilization=role.office,
        ))
    social_rate = norm_value(norms, "social_contributions_rate", name)
    lines.append(CostLine("social", "Соцотчисления", fot * social_rate, f"{social_rate:.1%} от ФОТ",
                          kind=ARTICLE_SHARE))

    # --- содержание партии и проезд ---
    for key, article in (
        ("field_allowance_per_person_month", "Полевое довольствие"),
        ("housing_per_person_month", "Жильё"),
        ("communication_per_person_month", "Связь"),
        ("household_per_person_month", "Бытовые расходы"),
    ):
        norm = norm_value(norms, key, name)
        lines.append(CostLine(key, article, headcount * months * norm,
                              f"{headcount} чел. × {months:.3f} мес × {norm:g} руб./чел.-мес"))
    travel = norm_value(norms, "travel_per_person", name)
    lines.append(CostLine("travel", "Проезд (туда-обратно)", headcount * travel,
                          f"{headcount} чел. × {travel:g} руб.", kind=ARTICLE_FIXED, mobilization=True))

    # --- топливо по пробегу ---
    liters = {"ai92": 0.0, "diesel": 0.0}
    for key, _label, norm_key, fuel in VEHICLES:
        liters[fuel] += float(values.get(f"mileage_{key}") or 0.0) / 100 * norm_value(norms, norm_key, name)
    for fuel, volume_l in liters.items():
        price = values.get(f"fuel_price_{fuel}")
        lacks = bool(volume_l) and price is None
        if lacks:
            missing.append(f"цена топлива: {FUEL_LABELS[fuel]}")
        lines.append(CostLine(f"fuel_{fuel}", f"Топливо ({FUEL_LABELS[fuel]})",
                              None if lacks else volume_l * float(price or 0.0),
                              f"{volume_l:.1f} л × {price if price is not None else '—'} руб./л"))

    # --- особые статьи раздела ---
    if spec.extra_lines is not None:
        extra, extra_missing, extra_warnings = spec.extra_lines(values, norms, context or {})
        lines += extra
        missing += extra_missing
        warnings += extra_warnings

    # --- суммы на объект ---
    for key, article in (("materials", "Материалы и снаряжение"), ("depreciation", "Амортизация"),
                         ("business_trip", "Командировочные")):
        amount = float(values.get(key) or 0.0)
        if amount:
            lines.append(CostLine(key, article, amount, "ввод оператора, сумма на объект",
                                  kind=ARTICLE_FIXED))
    if not values.get("depreciation") and not values.get("business_trip"):
        warnings.append("Амортизация и командировочные = 0: в расчётной книге они исключены — спорно, проверьте.")

    lines += shared_lines(shared, field_months)

    contingency = values.get("contingency_rate")
    direct, overhead, taxes, contingency_amount, cost, contingency_rate = close_cost(
        lines, fot, norms, None if contingency is None else float(contingency), name)
    assumed = [n.key for n in norms.values() if n.assumption and n.value is not None]
    if assumed:
        warnings.append(
            f"Нормы расчёта — ДОПУЩЕНИЯ ({len(assumed)}; источники — на странице «Справочники»), замените "
            "актуальными."
        )
    return CostResult(
        volume=volume, field_months=field_months, field_headcount=headcount, lines=tuple(lines),
        fot=fot, direct=direct, overhead=overhead, taxes=taxes, contingency=contingency_amount,
        cost=cost, missing=tuple(missing), warnings=tuple(warnings),
        contingency_rate=contingency_rate, volume_unit=spec.volume_unit, section=spec.code,
        fixed_what=spec.fixed_what, volume_word=spec.volume_word,
    )
