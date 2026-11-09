"""Интерфейс минимального бюджета (себестоимости) всех пяти разделов — 03.11.2026. Расчёт — `core/cost.py` (каркас) и
`modules/<раздел>/cost.py` (драйверы раздела); здесь только экраны.

Страница «4. Минимальный бюджет» (`render_minimum_page`), сверху вниз:
1. «Минимум по разделам и по объекту» — таблица всех пяти разделов (минимум,
   максимум, коэффициент, откуда ставки) и три числа по объекту: минимум
   объекта (общие затраты — один раз), сумма смет, коэффициент снижения;
2. «Общие затраты объекта» — мобилизация, доставка, база: вводятся один раз;
3. расчёт раздела, ВЫБРАННОГО В БОКОВОЙ ПАНЕЛИ (как на страницах 2 и 3):
   у геологии — прежний экран (`app/ui.py::render_geology_cost_section`), у
   остальных — типовой (`render_party_section`): общие ставки (оклады по
   должностям, цены топлива — одни на все разделы), состав партии и сроки,
   результат со статьями, контроль по архиву, панель «Сохранить расчёт».
Почему раздел из боковой панели, а не все пять списком: ввод раздела —
15–25 полей, пять форм подряд — длинная страница, в которой легко поправить
не тот раздел; выбор раздела уже есть в боковой панели и так же работает на
страницах 2 и 3; обзор всех разделов сразу даёт таблица п. 1.

Страница «5. Сводный бюджет» — `render_object_summary` (таблица по разделам и
три числа по объекту) под прежним блоком геологии.

Состояние: ввод раздела — `cost_inputs_by_module[код]` (словарь, как
`form_data_by_module`), общие ставки — `cost_inputs_common`, общие затраты —
`cost_shared`; виджеты — ключи `cost_<код>_<поле>`, `cost_common_<поле>`,
`cost_shared_<поле>`. Сохранение — при версии сметы раздела (`cost_calcs`,
как у геологии; схема базы не менялась): ввод раздела + общие ставки + общие
затраты (ключ "_shared").
"""

from __future__ import annotations

import streamlit as st

from app import ui
from core.cost import (
    CALIBRATION_NOTE,
    COMMON_FIELDS,
    FIELD_ROLES,
    KIND_FLOAT,
    KIND_INT,
    ROLES,
    CostResult,
    SharedCosts,
    coefficient_text,
    cost_input_defaults,
    cost_structure,
    fot_share_text,
    load_cost_norms,
    object_minimum,
    object_reduction,
    party_rates_status,
    reduction,
    suggested_party,
    widget_value,
)
from core.persistence import SmetaStoreBusyError
from core.pricing import calculate_total_with_vat
from core.stages import STATUS_CONFIRMED
from modules.ecology import cost as ecology_cost
from modules.geodesy import cost as geodesy_cost
from modules.geophysics import cost as geophysics_cost
from modules.hydromet import cost as hydromet_cost

# Разделы с типовым расчётом партии: код → модуль `modules/<раздел>/cost.py`.
PARTY_MODULES = {
    "geophysics": geophysics_cost,
    "geodesy": geodesy_cost,
    "ecology": ecology_cost,
    "hydromet": hydromet_cost,
}
SECTION_INPUTS_KEY = "cost_inputs_by_module"
COMMON_INPUTS_KEY = "cost_inputs_common"
SHARED_KEY = "cost_shared"

SHARED_FIELDS = (
    ("mobilization", "Организация и ликвидация работ (мобилизация), руб.",
     "Сборы, погрузка, оформление допусков — сумма на объект. Норм нет — ввод."),
    ("delivery", "Доставка техники и имущества до объекта, руб.",
     "Если разделы едут вместе: одна доставка на всех. Трал буровых геологии считается в "
     "геологии отдельно."),
    ("base_per_month", "База экспедиции, руб./мес",
     "Аренда / содержание общей базы; в минимум раздела — × месяцы его поля, в минимум объекта — "
     "один раз, по самому долгому полю."),
)
SHARED_RULE = (
    "Общие затраты вводятся один раз. В минимум каждого раздела они входят целиком (как если бы "
    "раздел выполнялся один — так честен коэффициент раздела), в минимум объекта — один раз: "
    "остаются у раздела, где они больше всего, у остальных исключаются. Правило — "
    "выбор правила — вопрос к руководству."
)


# --- ввод: словари состояния + текущие значения виджетов ----------------------------


def _merged(stored: dict, prefix: str, names) -> dict:
    """Ввод: сохранённый словарь, поверх — текущие значения виджетов (если они
    есть на этом прогоне), чтобы сводка вверху страницы не отставала на прогон."""
    values = dict(stored)
    for name in names:
        key = f"{prefix}{name}"
        if key in st.session_state:
            values[name] = st.session_state[key]
    return values


def common_values() -> dict:
    stored = st.session_state.setdefault(COMMON_INPUTS_KEY, {})
    return _merged(stored, "cost_common_", [f.name for f in COMMON_FIELDS])


def section_values(code: str) -> dict:
    stored = st.session_state.setdefault(SECTION_INPUTS_KEY, {}).setdefault(code, {})
    return _merged(stored, f"cost_{code}_", [f.name for f in PARTY_MODULES[code].SPEC.fields])


def shared_costs() -> SharedCosts:
    stored = st.session_state.setdefault(SHARED_KEY, {})
    return SharedCosts.from_dict(_merged(stored, "cost_shared_", [n for n, _l, _h in SHARED_FIELDS]))


def _defaults(name: str) -> dict:
    """Значения по умолчанию из локального слоя; битый слой — без них (предупреждение
    показывает расчёт норм)."""
    try:
        return cost_input_defaults(name)
    except (OSError, ValueError):
        return {}


def _run_cache() -> dict:
    """Кеш одного прогона страницы: сбрасывается, когда главный скрипт кладёт новый
    контекст прогона (`st.session_state["_run_context"]` — новый объект на каждом
    прогоне). Так разделы считаются один раз за прогон, а предупреждение о битом
    локальном слое показывается один раз."""
    context = st.session_state.get("_run_context")
    cache = st.session_state.get("_cost_run_cache")
    if cache is None or cache[0] is not context:
        cache = (context, {})
        st.session_state["_cost_run_cache"] = cache
    return cache[1]


def _norms(name: str):
    """Нормы раздела с общими; битый локальный слой — допущения и предупреждение
    (один раз за прогон)."""
    cache = _run_cache().setdefault("norms", {})
    if name not in cache:
        try:
            cache[name] = load_cost_norms(name)
        except (OSError, ValueError) as exc:
            st.warning(f"Локальный слой норм компании не прочитан, работают допущения: {exc}")
            cache[name] = load_cost_norms(name, use_local=False)
    return cache[name]


def _initial(field, stored, local):
    """Значение виджета при первом показе: сохранённое → локальный слой → пусто/0."""
    value = stored.get(field.name)
    if value is None and field.name not in stored and local is not None:
        value = local.value
    return widget_value(field.kind, value)


def _number_input(field, key, help_text=None):
    if field.kind == KIND_INT:
        return st.number_input(field.label, min_value=0, step=1, key=key, help=help_text or None)
    if field.kind == KIND_FLOAT:
        return st.number_input(field.label, min_value=0.0, key=key, help=help_text or None)
    return st.number_input(field.label, min_value=0.0, value=None, key=key, help=help_text or None)


def apply_section_cost_inputs(code: str, data: dict | None) -> None:
    """Сохранённый расчёт себестоимости раздела — в состояние (до отрисовки
    виджетов; открытие версии). `data=None` — у версии расчёта нет: ввод
    раздела очищается (общие ставки остаются — они одни на все разделы).
    Общие затраты объекта — из версии; нет в версии — сбрасываются в 0."""
    data = dict(data or {})
    shared = data.pop("_shared", None)
    if code in PARTY_MODULES:
        spec = PARTY_MODULES[code].SPEC
        own = {f.name: data[f.name] for f in spec.fields if f.name in data}
        st.session_state.setdefault(SECTION_INPUTS_KEY, {})[code] = own
        for f in spec.fields:
            st.session_state[f"cost_{code}_{f.name}"] = widget_value(f.kind, own.get(f.name))
        common = {f.name: data[f.name] for f in COMMON_FIELDS if f.name in data}
        if common:
            stored = st.session_state.setdefault(COMMON_INPUTS_KEY, {})
            stored.update(common)
            for f in COMMON_FIELDS:
                if f.name in common:
                    st.session_state[f"cost_common_{f.name}"] = widget_value(f.kind, common[f.name])
    apply_shared(shared)


def apply_shared(data: dict | None) -> None:
    """Общие затраты объекта — в состояние (до отрисовки виджетов)."""
    shared = SharedCosts.from_dict(data)
    st.session_state[SHARED_KEY] = shared.to_dict()
    for name, _label, _help in SHARED_FIELDS:
        st.session_state[f"cost_shared_{name}"] = float(getattr(shared, name))


# --- расчёт раздела ----------------------------------------------------------------


def _session(code: str):
    sessions = st.session_state.setdefault("staged_sessions", {})
    return sessions.get(code)


def confirmed_volumes(code: str):
    """Строки утверждённой ведомости раздела или None (не подтверждена)."""
    session = _session(code)
    if session is None:
        return None
    volumes = session.result("volumes")
    if volumes is None or session.status("volumes") != STATUS_CONFIRMED:
        return None
    return volumes.lines


def section_maximum(code: str) -> float | None:
    """Максимум раздела — смета по сборнику с индексом на дату расчёта, без НДС
    (как у геологии, `app/ui.py::geology_cost_numbers`); None — смета не готова."""
    session = _session(code)
    if session is None or not session.is_ready("estimate"):
        return None
    refs = ui.run_context().refs
    try:
        estimate = session.compute("estimate", refs).effective
        totals = calculate_total_with_vat(estimate, refs, work_type=code, date=ui.calc_date_iso(),
                                          index_policy=ui.index_policy())
    except (KeyError, ValueError, TypeError):
        return None  # ввод этапа сметы ещё не заполнен (обязательные параметры)
    return totals.indexed_total


def party_numbers(code: str, lines=None):
    """(расчёт, `Reduction`, статус ставок, объём) типового раздела по
    текущему вводу; None — ведомость не подтверждена."""
    lines = lines if lines is not None else confirmed_volumes(code)
    if lines is None:
        return None
    module = PARTY_MODULES[code]
    values = {**common_values(), **section_values(code)}
    norms = _norms(module.SPEC.norms_name)
    volume = module.volume_from_sheet(lines)
    shared = shared_costs()
    if code == "ecology":
        result = module.compute_cost(values, volume, norms, shared,
                                     samples=module.samples_from_sheet(lines))
    else:
        result = module.compute_cost(values, volume, norms, shared)
    status = party_rates_status(module.SPEC, values, norms, _defaults(module.SPEC.norms_name))
    return result, reduction(result, section_maximum(code)), status, volume


def geology_numbers():
    """(расчёт, `Reduction`, статус ставок) геологии по текущему вводу или None."""
    session = ui.geology_session()
    meters = ui.geology_drilling_meters(session)
    if meters is None:
        return None
    stored = st.session_state.get("cost_inputs_geology", {})
    values = _merged(stored, "cost_", [name for name, _l, _k in ui.COST_FIELDS])
    inputs, result, red = ui.geology_cost_numbers(session, meters, values)
    return result, red, ui.geology_rates_status(inputs)


def all_sections(geology=None):
    """Код → (расчёт, `Reduction`, статус) по всем разделам с утверждённой
    ведомостью — один расчёт на прогон страницы (`_run_cache`). `geology` — уже
    посчитанная на этом прогоне геология (страница 5: `ui_minimum.geology_numbers()`)."""
    cache = _run_cache()
    if "sections" in cache:
        return cache["sections"]
    numbers = {}
    geology = geology if geology is not None else geology_numbers()
    if geology is not None:
        numbers["geology"] = geology
    for code in PARTY_MODULES:
        party = party_numbers(code)
        if party is not None:
            numbers[code] = party[:3]
    cache["sections"] = numbers
    return numbers


def object_numbers(numbers=None):
    """Минимум объекта и коэффициент по объекту (`core/cost.py`)."""
    numbers = numbers if numbers is not None else all_sections()
    labels = {code: info["label"] for code, info in ui.MODULES.items()}
    minimum = object_minimum({c: n[0] for c, n in numbers.items()}, labels,
                             {c: n[2] for c, n in numbers.items()})
    maxima = {c: n[1].maximum for c, n in numbers.items()}
    return minimum, object_reduction(minimum, maxima)


# --- общие блоки -------------------------------------------------------------------


def render_sections_table(numbers) -> None:
    """Таблица «минимум по разделам»: все пять разделов, у кого что посчитано."""
    rows = []
    for code, info in ui.MODULES.items():
        if code not in numbers:
            rows.append({"Раздел": info["label"], "Минимум, руб.": None, "Максимум, руб.": None,
                         "Коэффициент": "—", "Ставки": "",
                         "Состояние": "ведомость не подтверждена"})
            continue
        result, red, status = numbers[code]
        rows.append({
            "Раздел": info["label"],
            "Минимум, руб.": round(result.cost, 2) if result.complete else None,
            "Максимум, руб.": None if red.maximum is None else round(red.maximum, 2),
            "Коэффициент": coefficient_text(red),
            "Ставки": "от бухгалтерии" if status.from_accounting else "допущения / цены",
            "Состояние": "посчитан" if result.complete else "не заполнено: " + ", ".join(result.missing),
        })
    ui.data_table(rows, hide_index=True, column_config={
        "Минимум, руб.": st.column_config.NumberColumn(format="localized"),
        "Максимум, руб.": st.column_config.NumberColumn(format="localized"),
    })


def render_object_metrics(minimum, red) -> None:
    cols = st.columns(3)
    cols[0].metric("Объект — минимум (себестоимость)", ui.money_text(minimum.total))
    cols[1].metric("Объект — максимум (сметы по СБЦ без НДС)", ui.money_text(red.maximum))
    cols[2].metric("Объект — коэффициент снижения", coefficient_text(red))
    for warning in red.warnings:
        st.warning(warning)
    if minimum.total is not None:
        st.caption(minimum.text())
        if minimum.duplicates_excluded:
            st.caption(f"Повторы общих затрат исключены из минимума объекта: "
                       f"{ui.rub(minimum.duplicates_excluded)} (с резервом на них).")
    st.caption(CALIBRATION_NOTE)


def render_summary_block(numbers) -> None:
    with ui.block("Минимум по разделам и по объекту"):
        st.caption(
            "Минимум — себестоимость (без НДС и прибыли): ниже неё работа в убыток. Максимум — "
            "смета по сборнику с индексом на дату расчёта, без НДС. Коэффициент = минимум / "
            "максимум. Раздел для ввода выбирается в боковой панели."
        )
        render_sections_table(numbers)
        minimum, red = object_numbers(numbers)
        render_object_metrics(minimum, red)


def render_shared_block() -> None:
    """Ввод общих затрат объекта (один раз на все разделы)."""
    stored = st.session_state.setdefault(SHARED_KEY, {})
    with ui.block("Ввод: общие затраты объекта (один раз на все разделы)"):
        st.caption(SHARED_RULE)
        cols = st.columns(3)
        values = {}
        for number, (name, label, help_text) in enumerate(SHARED_FIELDS):
            key = f"cost_shared_{name}"
            if key not in st.session_state:
                st.session_state[key] = float(stored.get(name) or 0.0)
            with cols[number % 3]:
                values[name] = st.number_input(label, min_value=0.0, step=10000.0, key=key,
                                               help=help_text)
    st.session_state[SHARED_KEY] = values


def render_structure(result: CostResult) -> None:
    """Таблица статей: сумма, доля, тип, как посчитано."""
    ui.data_table(
        [{"Статья": a.article,
          "Сумма, руб.": None if line.amount is None else round(a.amount, 2),
          "Доля, %": round(a.share * 100, 1) if result.cost else None,
          "Тип": a.kind, "Расчёт": line.basis}
         for a, line in zip(cost_structure(result), result.lines)],
        hide_index=True,
        column_config={"Сумма, руб.": st.column_config.NumberColumn(format="localized")},
    )


# --- раздел с типовым расчётом партии -------------------------------------------


def party_months_text(spec, volume, party, norms, override) -> str:
    """Фактический срок поля ПОСЛЕ подстановки состава: правило должностей может
    дать людей больше расчётного числа (у ИГДИ при 2 людях — 3 должности), тогда
    срок по норме короче подбора. Ручной срок — главнее нормы."""
    if override:
        return f"Срок поля — введённый: {float(override):g} мес."
    people = sum(party.get(role.key, 0) for role in FIELD_ROLES)
    rate = norms.get(spec.rate_norm_key) if spec.rate_norm_key else None
    if not people or rate is None or rate.value is None or volume <= 0:
        return "Срок поля введите вручную."
    return (f"С этим составом ({people} чел. в поле) срок по норме — "
            f"{volume / (float(rate.value) * people):.2f} мес.")


def _fill_party(code: str, party: dict) -> None:
    """Обработчик «Подставить состав по норме»: численность по должностям."""
    for role in ROLES:
        st.session_state[f"cost_{code}_{role.key}"] = int(party.get(role.key, 0))


def render_party_section(code: str) -> None:
    """Себестоимость раздела без бурения: общие ставки → состав и сроки →
    результат → контроль по архиву → панель «Сохранить расчёт себестоимости»."""
    module = PARTY_MODULES[code]
    spec = module.SPEC
    store = ui.run_context().store
    session = _session(code)
    flash = st.session_state.pop(f"flash_cost_{code}", None)
    if flash is not None:
        (st.success if flash[0] == "ok" else st.error)(flash[1])
    st.subheader(spec.label)
    lines = confirmed_volumes(code)
    if lines is None:
        st.info("Расчёт себестоимости — после подтверждения ведомости объёмов раздела "
                f"(нужен объём: {spec.volume_label}).")
        with ui.action_panel():
            ui.primary_action(
                "Сохранить расчёт себестоимости", f"save_cost_{code}",
                f"Расчёт сохраняется при версии сметы раздела «{spec.label}».",
                [f"не подтверждена ведомость объёмов раздела «{spec.label}»"],
            )
            st.page_link(ui.page_path(ui.PAGE_VOLUMES), label="Перейти: 2. Ведомость объёмов",
                         icon=":material/arrow_forward:")
        return

    norms = _norms(spec.norms_name)
    local = _defaults(spec.norms_name)
    volume = module.volume_from_sheet(lines)

    # --- общие ставки ---
    common_stored = st.session_state.setdefault(COMMON_INPUTS_KEY, {})
    common = {}
    with ui.block("Ввод: общие ставки (одни для геодезии, геофизики, экологии, гидрометеорологии)"):
        st.caption(
            "Оклады по должностям и цены топлива задаются один раз и действуют во всех разделах без "
            "бурения. По умолчанию — ставки компании этого компьютера, "
            "если он заполнен на этом ПК; иначе пусто — введите."
        )
        cols = st.columns(3)
        for number, field in enumerate(COMMON_FIELDS):
            key = f"cost_common_{field.name}"
            if key not in st.session_state:
                st.session_state[key] = _initial(field, common_stored, local.get(field.name))
            hint = local.get(field.name)
            with cols[number % 3]:
                common[field.name] = _number_input(
                    field, key, (f"По умолчанию — локально: {hint.source}" if hint else field.help))
    st.session_state[COMMON_INPUTS_KEY] = common

    # --- состав партии, сроки, транспорт ---
    stored = st.session_state.setdefault(SECTION_INPUTS_KEY, {}).setdefault(code, {})
    own = {}
    with ui.block("Ввод: состав партии, сроки, транспорт"):
        rate_text = ("нормы выработки нет — срок полевых работ вводится" if spec.rate_norm_key is None
                     else f"норма выработки {norms[spec.rate_norm_key].value:g} "
                          f"{norms[spec.rate_norm_key].unit} (ДОПУЩЕНИЕ, источник — «Справочники»)")
        st.caption(
            f"Объём по утверждённой ведомости: {volume:g} {spec.volume_unit} ({spec.volume_label}); "
            f"{rate_text}. Срок поля = объём / (норма × численность поля), если срок не задан."
        )
        override = st.session_state.get(f"cost_{code}_field_months")
        plan_months = float(override or 1.0)
        party = suggested_party(spec, volume, plan_months, norms)
        if party:
            labels = {r.key: r.label.lower() for r in ROLES}
            st.caption(
                f"По правилу ({spec.rule_source}), подбор на срок поля {plan_months:g} мес: "
                + ", ".join(f"{labels[k]} — {n}" for k, n in party.items() if n) + ". "
                + party_months_text(spec, volume, party, norms, override)
            )
            st.button("Подставить состав по норме", key=f"suggest_party_{code}",
                      on_click=_fill_party, args=(code, party),
                      help="Заполнит численность по должностям; поправить можно вручную.")
        cols = st.columns(3)
        for number, field in enumerate(spec.fields):
            key = f"cost_{code}_{field.name}"
            if key not in st.session_state:
                st.session_state[key] = _initial(field, stored, local.get(field.name))
            with cols[number % 3]:
                own[field.name] = _number_input(field, key, field.help)
    st.session_state[SECTION_INPUTS_KEY][code] = own

    result, red, status, _volume = party_numbers(code, lines)

    with ui.block("Результат"):
        if red.warnings:
            st.warning("\n\n".join(red.warnings))
        if result.missing:
            st.info("Не заполнено: " + ", ".join(result.missing))
        cols = st.columns(3)
        cols[0].metric("Максимум: смета по СБЦ (без НДС)", ui.money_text(red.maximum))
        cols[1].metric("Минимум: себестоимость", ui.money_text(red.minimum))
        cols[2].metric("Коэффициент снижения", coefficient_text(red))
        st.caption(ui.REDUCTION_NOTE)
        if not red.calibrated:
            ui.render_rates_status(status)
        st.markdown("**Себестоимость по статьям**")
        render_structure(result)
        months = "—" if result.field_months is None else f"{result.field_months:.2f}".replace(".", ",")
        per_unit = result.cost_per_unit
        st.write(
            f"Полевые работы: {months} мес, полевая группа {result.field_headcount} чел. Прямые "
            f"затраты {ui.rub(result.direct)}; себестоимость **{ui.rub(result.cost)}**"
            + (f"; на единицу объёма ({spec.volume_unit}) — {ui.rub(per_unit)}"
               if per_unit and spec.rate_norm_key else "")
        )
        fot_text = fot_share_text(result)
        if fot_text:
            st.caption(fot_text)
        for warning in result.warnings:
            st.caption(warning)

    with ui.block("Контроль по архиву (вариант А)"):
        st.info(spec.archive_note)

    with ui.action_panel():
        version_id = session.version_id if session is not None else None
        blockers = [] if version_id is not None else [
            "ведомость не сохранена версией — подтвердите ведомость"]
        if ui.primary_action(
            "Сохранить расчёт себестоимости", f"save_cost_{code}",
            (f"Ввод, общие ставки, общие затраты и итоги сохраняются при последней версии сметы "
             f"раздела (#{version_id}).") if version_id is not None else
            "Ввод и итоги сохраняются при версии сметы раздела.",
            blockers,
        ):
            summary = {
                "volume": volume, "cost": result.cost, "complete": result.complete,
                "missing": list(result.missing), "fot": result.fot, "direct": result.direct,
                "maximum": red.maximum, "coefficient": red.coefficient,
                "calc_date": ui.calc_date_iso(),
            }
            inputs = {**common, **own, "_shared": shared_costs().to_dict()}
            try:
                store.save_cost_calc(version_id, code, inputs, summary)
                st.session_state[f"flash_cost_{code}"] = (
                    "ok", f"Расчёт себестоимости сохранён при версии #{version_id}.")
            except SmetaStoreBusyError as exc:
                st.session_state[f"flash_cost_{code}"] = ("error", f"Не сохранено: {exc}")
            st.rerun()


# --- страницы -------------------------------------------------------------------


def render_minimum_page() -> None:
    """Страница «4. Минимальный бюджет» (см. докстринг модуля)."""
    code = st.session_state.get("module_choice") or next(iter(ui.MODULES))
    render_summary_block(all_sections())
    render_shared_block()
    st.caption(f"Раздел для расчёта — «{ui.MODULES[code]['label']}» (выбор — в боковой панели).")
    if code == "geology":
        ui.render_geology_cost_section(ui.geology_session())
    else:
        render_party_section(code)


def render_object_summary(geology=None) -> None:
    """Страница «5. Сводный бюджет»: минимум по разделам и по объекту. `geology` —
    себестоимость геологии, уже посчитанная на этом прогоне (не считать дважды)."""
    numbers = all_sections(geology)
    st.markdown("**Все разделы**")
    render_sections_table(numbers)
    minimum, red = object_numbers(numbers)
    render_object_metrics(minimum, red)


def render_norms_tables() -> None:
    """Страница «Справочники», вкладка норм: общие нормы и особые нормы разделов
    без бурения (только чтение, с источниками)."""
    from core.norms import ORIGIN_LOCAL, load_norms

    groups = [("Общие нормы всех разделов", "cost_common")] + [
        (module.SPEC.label, module.SPEC.norms_name)
        for module in PARTY_MODULES.values()
    ]
    for title, name in groups:
        try:
            own = load_norms(name, use_local=False)
            norms = load_cost_norms(name)
        except (OSError, ValueError) as exc:
            st.warning(f"{title}: нормы не прочитаны: {exc}")
            continue
        st.markdown(f"**{title}**")
        ui.data_table(
            [{"Норма": key, "Значение": norms[key].value, "Ед.": norms[key].unit,
              "Откуда": "локально (компании)" if norms[key].origin == ORIGIN_LOCAL else "допущение",
              "Источник": norms[key].source, "Примечание": norms[key].note}
             for key in own],
            hide_index=True,
        )
