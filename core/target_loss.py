"""Цена против минимального бюджета объекта: «работа в убыток на N руб.».

Одно предупреждение на страницу «5. Сводный бюджет» и лист «КП» Excel (Доработка 03.11.2026; связка с минимумом всех разделов —). Минимум —
`core.cost.ObjectMinimum` (трек «минимум»): сумма себестоимостей разделов с полным
расчётом, общие затраты (проезд, трал…) — один раз; неполные разделы перечислены.
Своего класса минимума здесь нет — одна сущность на программу.

Какая цена сравнивается:
- задана целевая цена (с НДС) — цена при ней: k* = целевая / итог с НДС при
  коэффициенте сводной 1 (итог линеен по k, `BudgetAggregator.fit_budget_reduction`),
  не больше 1 — выше сметы коэффициентом снижения цену не поднять («цена ограничена
  сметой»); если в поле коэффициента сводной стоит меньше k* — цена сводной и листа
  «КП» ниже целевой, и сравнивается она (меньшая из двух цен: если не в убытке она,
  не в убытке и бо́льшая);
- целевой нет, но задано снижение — текущая цена после снижения (раздел × сводная);
- ни того, ни другого — смета; предупреждение — только если минимум выше самой сметы
  («работа в убыток при любом снижении; снижение невозможно»).

Сопоставимость: минимум — себестоимость БЕЗ НДС (НДС не покрывает затраты), поэтому и
цена — без НДС. Сравниваются только разделы, у которых минимум посчитан: цена этих
разделов = k × сумма их цен после снижения раздела. Если минимум посчитан не по всем
разделам бюджета — оговорка «минимум посчитан только по …»; неполные разделы —
«… неполный: не заполнено …». Дополнительно — разделы, чья цена ниже их собственного
минимума (минимум раздела — со всеми его общими затратами), даже если объект в целом
не в убытке: «ниже минимума раздела».
"""

from __future__ import annotations

from dataclasses import dataclass

LOSS_PREFIX = "Работа в убыток на"
SECTION_PREFIX = "Цена ниже минимума раздела"
NOT_CALIBRATED_NOTE = "минимум не калиброван, оценка условная"


def _rub(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " руб."


def _num(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


@dataclass(frozen=True)
class TargetLoss:
    """Результат сравнения: `loss` — на сколько цена (без НДС) ниже минимума объекта
    (0 — объект не в убытке, но есть раздел ниже своего минимума); `text` — готовое
    предупреждение для экрана и листа «КП»."""

    loss: float
    price_without_vat: float
    minimum: float
    text: str


def target_loss(aggregator, target_with_vat, minimum, skipped: tuple[str, ...] = ()) -> TargetLoss | None:
    """Предупреждение «работа в убыток» / «цена ниже минимума раздела» или None.

    `minimum` — `core.cost.ObjectMinimum` по разделам бюджета (вызывающий
    отбрасывает разделы, которых в бюджете нет — их названия в `skipped`, для
    оговорки); `target_with_vat` — целевая цена или None (тогда — текущая цена
    после снижения; снижения нет — смета, и предупреждение только если минимум
    выше самой сметы: «снижение невозможно»)."""
    if minimum is None:
        return None
    before = aggregator.total_before_budget_reduction()
    if before is None or before <= 0:
        return None
    priced = [m for m in aggregator.summary()["modules"] if m.total_with_vat is not None]
    capped = False
    field_note = None
    if target_with_vat and target_with_vat > 0:  # 0 / отрицательная — «не задана»
        k = float(target_with_vat) / before
        capped = k > 1.0
        k = min(k, 1.0)
        basis = "целевая цена"
        if aggregator.budget_reduction < k - 1e-12:
            # В поле коэффициента сводной — снижение сильнее, чем нужно для целевой
            # цены: в сводную и на лист «КП» идёт цена по полю, она ниже целевой —
            # её и сравниваем (аудит 05.11.2026, О-4: раньше при введённой целевой
            # цене на коэффициент в поле не смотрели, и убыток по цене «КП» молчал).
            k = aggregator.budget_reduction
            basis = "цена после снижения"
            capped = False
            field_note = (f"Целевая цена ({_rub(float(target_with_vat))} с НДС) выше цены "
                          f"сводной: в поле коэффициента сводной стоит {_num(k, 4)} — итог "
                          "сводной и лист «КП» считаются по нему, а не по целевой цене.")
    elif aggregator.has_reduction():
        k = aggregator.budget_reduction
        basis = "цена после снижения"
    else:
        k = 1.0
        basis = "смета без снижения"
    incomplete_names = {label for label, _missing in minimum.incomplete}
    if minimum.total is None:
        # ни у одного раздела бюджета минимум не полный: сказать, а не молчать
        # (только когда есть с чем сравнивать — целевая цена или снижение)
        if basis == "смета без снижения" or not minimum.incomplete:
            return None
        return TargetLoss(0.0, 0.0, 0.0, "Минимум не посчитан — сравнить цену не с чем: "
                          + "; ".join(f"у раздела «{label}» расчёт себестоимости неполный (не "
                                      f"заполнено: {', '.join(missing)})"
                                      for label, missing in minimum.incomplete)
                          + ". Заполните страницу «4. Минимальный бюджет».")
    own = {s.code: s for s in minimum.sections}
    covered = [m for m in priced if m.module_code in own]
    if not covered:
        return None
    base = sum(m.reduced_total for m in covered)        # после снижения разделов
    indexed = sum(m.indexed_total for m in covered)     # смета с индексом, без снижений
    price = k * base
    names = ", ".join(own[m.module_code].label for m in covered)
    below = [(own[m.module_code].label, k * m.reduced_total, own[m.module_code].cost)
             for m in covered if k * m.reduced_total < own[m.module_code].cost]
    partial = len(covered) < len(priced)
    if price < minimum.total:
        loss = minimum.total - price
        what = f"{basis} разделов с посчитанным минимумом ({names})" if partial else basis
        parts = [f"{LOSS_PREFIX} {_rub(loss)}: {what} — {_rub(price)} без НДС — ниже минимума "
                 f"(себестоимости) {_rub(minimum.total)}"]  # «руб.» — конец предложения
        if minimum.total > indexed:
            parts.append("Себестоимость выше самой сметы (с индексом, без снижений): работа в "
                         "убыток при любом снижении; снижение невозможно.")
        elif base > 0 and minimum.total / base > 1:
            parts.append("Снижение сводной не спасает: минимум выше цены уже после снижения "
                         "разделов — уменьшите снижение раздела (коэффициенты разделов на этой "
                         "странице).")
        elif basis == "цена после снижения" and base > 0:
            parts.append(f"Коэффициент сводной для этих разделов должен быть не ниже "
                         f"{_num(minimum.total / base)}.")
        if below and len(covered) > 1:
            parts.append("В том числе ниже минимума раздела: " + "; ".join(
                f"{label} — {_rub(p)} при минимуме {_rub(c)}" for label, p, c in below)
                + " (минимум раздела — со всеми его общими затратами).")
    elif below and len(covered) > 1:
        loss = 0.0
        parts = [f"{SECTION_PREFIX}: объект в целом не в убытке ({basis} {_rub(price)} без НДС не "
                 f"ниже минимума {_rub(minimum.total)}), но ниже минимума раздела: "
                 + "; ".join(f"{label} — {_rub(p)} при минимуме {_rub(c)}" for label, p, c in below)
                 + " (минимум раздела — со всеми его общими затратами)."]
    else:
        return None
    if partial:
        rest = [m.module_name for m in priced
                if m.module_code not in own and m.module_name not in incomplete_names]
        line = f"Минимум посчитан только по: {names}."
        if rest:
            line += f" По остальным разделам бюджета ({', '.join(rest)}) не проверено."
        line += " Доля цены — при одинаковом снижении всех разделов."
        parts.append(line)
    for label, missing in minimum.incomplete:
        parts.append(f"Минимум раздела «{label}» неполный (не заполнено: {', '.join(missing)}) — "
                     "в сумму не вошёл.")
    if skipped:
        parts.append(f"Минимум посчитан и по разделам вне бюджета ({', '.join(skipped)}) — они не "
                     "сравниваются: сметы в бюджете нет.")
    if minimum.shared:
        parts.append("Общие затраты нескольких разделов учтены в минимуме один раз.")
    if field_note:
        parts.append(field_note)
    if capped:
        parts.append("Целевая цена выше сметы — цена ограничена сметой (без снижения сводной): "
                     "выше сметы коэффициентом снижения её не поднять.")
    parts.append("Сравнение — без НДС: себестоимость считается без НДС.")
    if not minimum.calibrated:
        parts.append(f"{NOT_CALIBRATED_NOTE[0].upper()}{NOT_CALIBRATED_NOTE[1:]}.")
    return TargetLoss(loss, price, minimum.total, " ".join(parts))
