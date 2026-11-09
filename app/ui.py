"""Общий код интерфейса расчёта бюджета ПИР — для главного скрипта
`app/streamlit_app.py` и страниц меню `app/app_pages/*.py`.

Меню повторяет процесс компании (29.10.2026): 1. Объект и ТЗ → 2. Ведомость
объёмов → 3. Максимальный бюджет (смета) → 4. Минимальный бюджет → 5. Сводный
бюджет; плюс «Справочники» и «Версии». Расчёт при этом не менялся — функции
ниже перенесены из прежнего одностраничного `streamlit_app.py`.

Каждый модуль описан списком этапов (core/stages.py). Все пять
модулей — двухэтапные (ведомость объёмов → смета): этап 1 — страница
«Ведомость объёмов», этап 2 — «Максимальный бюджет»; каждое подтверждение
сохраняется версией в цепочке (core/persistence.py::SmetaStore.save_version).
Ветка одноэтапного сценария "форма → Рассчитать → смета" (адаптер `as_staged`
для модуля без `stages()`) сохранена для будущих модулей, у текущих не
используется."""

import io
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone

# core/ и modules/ — пакеты проекта (pyproject.toml). Импортируются, когда
# корень проекта в sys.path: `python -m streamlit run app/streamlit_app.py`
# из корня (так запускает launch_app.bat) или после `pip install -e .`
# (setup.bat) — тогда и просто `streamlit run`. В exe (PyInstaller) пакеты
# лежат в корне распаковки, который в sys.path и так.
import streamlit as st

from app import theme, ui_help, ui_target, ui_tz_keys
from core import param_rules
from core.accruals import MISSING_ACCRUALS_PREFIX, accrual_breakdown
from core.aggregator import BudgetAggregator, ModuleBudget, mobilization_base
from core.cost import cost_input_defaults, load_cost_norms
from core.display import split_label, split_warnings
from core.export_excel import export_file_name, export_to_excel
from core.formatting import decimals_for_unit, format_quantity
from core.models import (
    PLACEMENT_AFTER_INDEX,
    PLACEMENT_BEFORE_INDEX,
    PLACEMENT_LABELS,
    calculate_amount,
)
from core.module_base import Field
from core.norms import ORIGIN_LOCAL
from core.object_data import (
    ACCRUAL_ETALON_HINTS,
    ACCRUAL_FIELDS,
    ACCRUAL_MODULES,
    ACCRUAL_RATE_HELP,
    ACCRUAL_RATE_LABELS,
    BASE_OBJECT_FIELDS,
    OBJECT_FIELDS,
    REDUCTION_LABELS,
    TRANSPORT_MODULES,
    ObjectData,
)
from core.overrides import (
    BASIS_KIND_LABELS,
    BASIS_PRICE_LIST,
    FIELD_CUSTOM,
    FIELD_CUSTOM_DELETE,
    OverrideLog,
    custom_without_basis,
    display_basis,
    is_custom_id,
)
from core.persistence import SmetaStore, SmetaStoreBusyError
from core.pricing import INDEX_POLICY_LAST_KNOWN, INDEX_POLICY_STRICT, calculate_total_with_vat
from core.references import DEFAULT_DB_PATH as REFERENCES_DB_PATH, ReferenceBook, ReferencesError, stale_reason
from core.stages import (
    SOURCE_LABELS,
    STATUS_CONFIRMED,
    STATUS_DRAFT,
    STATUS_LABELS,
    STATUS_STALE,
    SnapshotFormatError,
    StagedModule,
    StagedSession,
    StageNotReady,
    VolumeLine,
    as_staged,
    default_editable_fields,
    fingerprint,
    numbers_fingerprint,
)
from core.tz import (
    INTEGER_FIELDS as INTEGER_TZ_FIELDS,
    MODE_FILL_EMPTY,
    MODE_LABELS,
    NUMBER_FIELDS as NUMBER_TZ_FIELDS,
    OBJECT_KINDS,
    PROPOSE_KEY,
    PROPOSE_TZ,
    SECTION_LABELS,
    SURVEY_SCALES,
    TZ_LABELS,
    TzParams,
    merge_proposal,
    propose_stage_input,
    provenance_rows,
)
from core.validation import below_min_fields, empty_volume_sheet, missing_required_fields
from modules.ecology.module import EcologyModule
from modules.geodesy.module import (
    VK_TOPO_PLANS,
    VK_TOPO_SURVEYS,
    GeodesyModule,
    match_topo_survey_area,
    match_topo_survey_section,
)
from modules.geology.cost import (
    ARCHIVE_CAPTION,
    DEMO_ARCHIVE_NOTE,
    NORMS_NAME,
    CostInputs,
    archive_by_proximity,
    archive_disclaimer,
    archive_summary,
    coefficient_text,
    compute_cost,
    cost_structure,
    fot_share_text,
    is_demo_archive,
    load_archive,
    rates_status,
    reduction,
)
from modules.geology.module import DRILLING_VOLUME_KEY_PREFIX, GeologyModule
from modules.geophysics.module import GeophysicsModule
from modules.hydromet.module import HydrometModule

MODULES = {
    "geology": {"label": "Геология (ОИГ)", "class": GeologyModule},
    "geophysics": {"label": "Геофизика (ИГФИ)", "class": GeophysicsModule},
    "geodesy": {"label": "Геодезия (ИГДИ)", "class": GeodesyModule},
    "ecology": {"label": "Экология (ИЭИ)", "class": EcologyModule},
    "hydromet": {"label": "Гидрометеорология (ИГМИ)", "class": HydrometModule},
}

# Страницы меню: ключ → (файл относительно главного скрипта, заголовок, иконка).
# Порядок — порядок пунктов меню; первая страница — открывается по умолчанию.
PAGE_OBJECT = "obekt"
PAGE_VOLUMES = "vedomost"
PAGE_ESTIMATE = "smeta"
PAGE_MINIMUM = "minimum"
PAGE_BUDGET = "svodny"
PAGE_REFERENCES = "spravochniki"
PAGE_VERSIONS = "versii"
PAGES = {
    PAGE_OBJECT: ("app_pages/p1_obekt.py", "1. Объект и ТЗ", ":material/location_on:"),
    PAGE_VOLUMES: ("app_pages/p2_vedomost.py", "2. Ведомость объёмов", ":material/checklist:"),
    PAGE_ESTIMATE: ("app_pages/p3_smeta.py", "3. Максимальный бюджет", ":material/request_quote:"),
    PAGE_MINIMUM: ("app_pages/p4_minimum.py", "4. Минимальный бюджет", ":material/savings:"),
    PAGE_BUDGET: ("app_pages/p5_svodny.py", "5. Сводный бюджет", ":material/summarize:"),
    PAGE_REFERENCES: ("app_pages/p6_spravochniki.py", "Справочники", ":material/menu_book:"),
    PAGE_VERSIONS: ("app_pages/p7_versii.py", "Версии", ":material/history:"),
}
# Разделы левого меню (docs/ui_guide.md §1): шаги процесса — по порядку, служебное — ниже.
MENU_SECTIONS = {
    "Шаги процесса": [PAGE_OBJECT, PAGE_VOLUMES, PAGE_ESTIMATE, PAGE_MINIMUM, PAGE_BUDGET],
    "Служебное": [PAGE_REFERENCES, PAGE_VERSIONS],
}


def page_path(page_key):
    return PAGES[page_key][0]


# --- каркас страницы (docs/ui_guide.md §2–§5) ------------------------------------
# Заголовок шага + «что здесь делать» → блоки в рамках (ввод, результат) →
# панель действий с одной главной кнопкой. Страницы и экраны этапов вызывают
# только эти функции, чтобы порядок и вид были везде одинаковыми.


def money_text(value):
    """Рубли с разделителями разрядов и копейками: «28 449 810,51»; None — «—»."""
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def rub(value):
    """«28 449 810,51 руб.»"""
    return f"{money_text(value)} руб."


STEP_COUNT = len(MENU_SECTIONS["Шаги процесса"])


def step_number(page_key):
    """Номер шага процесса (1–5) или None у служебных страниц."""
    steps = MENU_SECTIONS["Шаги процесса"]
    return steps.index(page_key) + 1 if page_key in steps else None


def nav_title(page_key):
    """Подпись пункта меню: у шагов без номера — номер рисуется в квадрате рядом
    (components/StepNav.md), у служебных — как в заголовке страницы."""
    title = PAGES[page_key][1]
    return title.split(". ", 1)[1] if step_number(page_key) else title


def page_key_by_title(title):
    """Ключ страницы по заголовку шага «N. …» (страницы вызывают page_header с ним)."""
    number = title.split(".", 1)[0]
    if number.isdigit() and 1 <= int(number) <= STEP_COUNT:
        return MENU_SECTIONS["Шаги процесса"][int(number) - 1]
    return None


def _stage_status(module_code, stage_index):
    """(вид, текст) статуса этапа выбранного раздела для меню и шапки шага — только по
    статусу этапа, без пересчёта (дешёво на каждом прогоне)."""
    session = st.session_state.get("staged_sessions", {}).get(module_code)
    if session is None:
        return ("todo", "не начат")
    stages = session.staged.stages
    if stage_index >= len(stages):
        return None
    stage = stages[stage_index]
    status = session.status(stage.key)
    if status == STATUS_STALE:
        return ("warn", "устарел")
    if status == STATUS_CONFIRMED:
        return ("done", "подтверждён")
    return ("todo", STATUS_LABELS[status])


def step_status(page_key):
    """(вид, текст) статуса шага: done / warn / error / todo; None — статуса нет."""
    if page_key == PAGE_OBJECT:
        object_data = run_context().object_data
        if object_data.warnings():
            return ("warn", "есть предупреждения")
        return ("done", "заполнен") if (object_data.code or object_data.name) else ("todo", "не заполнен")
    module_code = st.session_state.get("module_choice") or next(iter(MODULES))
    if page_key == PAGE_VOLUMES:
        return _stage_status(module_code, 0)
    if page_key == PAGE_ESTIMATE:
        return _stage_status(module_code, 1)
    if page_key == PAGE_BUDGET:
        sessions = [s for s in st.session_state.get("staged_sessions", {}).values() if s is not None]
        statuses = [s.last_stage_status() for s in sessions if s.result(s.staged.stages[-1].key)]
        if STATUS_STALE in statuses:
            return ("warn", "есть устаревшие сметы")
        confirmed = statuses.count(STATUS_CONFIRMED)
        return ("done", f"разделов: {confirmed}") if confirmed else ("todo", "нет подтверждённых смет")
    return None


def page_header(title, what_to_do):
    """Шапка шага (components/PageHeader.md): «Шаг N из 5», заголовок, одна строка
    «что здесь делать», объект, дата расчёта и статус шага — на фирменном градиенте."""
    page_key = page_key_by_title(title)
    object_data = run_context().object_data
    obj = " — ".join(v for v in (object_data.code, object_data.name) if v) or "не заполнен"
    meta = [("Объект", obj), ("Дата расчёта", f"{st.session_state['calc_date']:%d.%m.%Y}")]
    if page_key in (PAGE_VOLUMES, PAGE_ESTIMATE):
        module_code = st.session_state.get("module_choice") or next(iter(MODULES))
        meta.append(("Раздел", MODULES[module_code]["label"]))
    status = step_status(page_key) if page_key else None
    with st.container():
        st.html(theme.mark("header") + theme.header_kicker(step_number(page_key) if page_key else None, STEP_COUNT))
        st.header(title, anchor=False)
        st.caption(what_to_do)
        st.html(theme.header_meta(meta, status))
    # Справка «Как работать с этим шагом» — из app/help_texts.json (трек 5, app/ui_help.py).
    ui_help.render_page_help(title, is_step=page_key is not None)


def inject_theme(pages):
    """Тема дизайн-кода на этом прогоне (app/theme): стили, логотип, номера шагов и
    значки статусов в меню. `pages` — {ключ страницы: st.Page}."""
    steps = []
    for key, page in pages.items():
        url_path = "" if key == PAGE_OBJECT else page.url_path
        status = step_status(key)
        steps.append((url_path, step_number(key), status[0] if status else None))
    theme.inject(steps)


def block(title):
    """Блок страницы в рамке с заголовком (components/Panel.md): ввод, результат, правки…"""
    box = st.container(border=True)
    box.html(theme.mark("panel"))
    box.markdown(f"**{title}**")
    return box


def action_panel():
    """Панель «Действие» (components/ActionPanel.md) — отдельная рамка внизу
    страницы/этапа, с верхней изумрудной линией."""
    box = st.container(border=True)
    box.html(theme.mark("action"))
    box.markdown("**Действие**")
    return box


def primary_action(label, key, explanation, blockers=()):
    """Главная кнопка страницы (`type="primary"`), под ней — что произойдёт.
    `blockers` — причины, по которым действие сейчас невозможно: тогда кнопка
    недоступна, причины — рядом. Вызывать внутри `action_panel()`."""
    text, button = st.columns([3, 2], vertical_alignment="center")
    text.caption(explanation)
    with button:
        clicked = st.button(label, key=key, type="primary", disabled=bool(blockers))
    if blockers:
        # :orange[…] — цвет warning дизайн-кода (orangeColor темы, app/theme/build_theme.py).
        st.caption(":orange[! Сейчас недоступно: " + "; ".join(blockers) + ".]")
    return clicked and not blockers


# Звёздочка экранирована: «* —» в начале строки Markdown рисует как пункт списка.
REQUIRED_NOTE = r"\* — обязательное поле"


def secondary_actions_label():
    """Черта и подпись строки второстепенных действий в панели действий."""
    st.html(theme.separator())
    st.caption("Ещё:")


def stage_page(staged, stage_key):
    """Страница меню, на которой редактируется этап: первый этап (ведомость)
    и единственный этап одноэтапного модуля — «Ведомость объёмов», более
    поздние — «Максимальный бюджет»."""
    return PAGE_VOLUMES if staged.index(stage_key) == 0 else PAGE_ESTIMATE


def go_to_stage(staged, session):
    """Перейти на страницу текущего этапа сессии (после подтверждения,
    возврата на этап, открытия версии). На той же странице — просто
    перерисовка."""
    st.switch_page(page_path(stage_page(staged, session.current)))


# --- контекст прогона -------------------------------------------------------
#
# Справочник, база смет и данные объекта нужны почти всем функциям ниже.
# Раньше это были глобальные переменные одностраничного скрипта; теперь
# главный скрипт кладёт их в session_state на каждом прогоне (не в
# переменные модуля: модуль один на процесс, а вкладок браузера с разными
# объектами может быть несколько).


@dataclass(frozen=True)
class RunContext:
    refs: object          # CalculationContext(справочник, данные объекта)
    base_refs: object     # ReferenceBook без данных объекта
    store: SmetaStore
    object_data: ObjectData


def run_context() -> RunContext:
    return st.session_state["_run_context"]


def object_data_from_state() -> ObjectData:
    """Данные объекта из значений виджетов блока «Данные объекта»
    (`obj_<поле>`, страница «Объект и ТЗ»). Виджеты рисуются только на своей
    странице, но с `persist_state="session"` — значения живут всю сессию,
    поэтому собрать данные можно на любой странице."""
    values = {}
    for field in OBJECT_FIELDS:
        if field.type == "accrual_table":
            values[field.name] = {
                module: {name: st.session_state.get(accrual_override_key(module, name))
                         for name in ACCRUAL_RATE_LABELS}
                for module in ACCRUAL_MODULES
            }
            continue
        if field.type == "reduction_table":
            values[field.name] = {
                key: st.session_state.get(reduction_key(key)) for key in REDUCTION_LABELS
            }
            continue
        if field.type == "distance_table":
            values[field.name] = {
                module: st.session_state.get(transport_distance_key(module))
                for module in TRANSPORT_MODULES
            }
            continue
        if field.type == "tz_table":
            values[field.name] = tz_from_state().to_dict()
            continue
        raw = st.session_state.get(f"obj_{field.name}")
        if field.type == "date":
            raw = raw.isoformat() if raw else None
        elif field.type == "bool":
            raw = bool(raw)
        elif field.type != "number":
            raw = raw or ""
        values[field.name] = raw
    return ObjectData.from_dict(values)


def field_label(field: Field, invalid: bool) -> str:
    """Подпись поля: "*" у обязательных — всегда, нейтрально (без
    цвета). Красный (markdown-разметка Streamlit `:red[...]`) — только
    когда вызывающий код явно решил, что поле сейчас невалидно
    (`invalid=True`). Сегодня единственная причина invalid —
    "обязательное поле не заполнено после нажатия «Рассчитать»"
    (см. вызов ниже), но сам хелпер не завязан на эту причину: он
    просто красит то, что ему передали, и годится и для будущей
    проверки "заполнено, но неверно" (диапазоны и т.п.) без изменений
    сюда — только новый источник `invalid` у вызывающего кода."""
    short, _hint = split_label(field.label)
    text = f"{short} *" if field.required else short
    return f":red[{text}]" if invalid else text


def number_min_value(field, value):
    """`min_value` для st.number_input по `Field.min_value` — того же типа,
    что и значение (Streamlit требует одинаковые типы). Если значение уже
    меньше минимума (старая сохранённая смета), минимум опускается до
    него: иначе Streamlit падает, а так оператор видит значение и ошибку
    проверки (core/validation.py::below_min_fields)."""
    if field.min_value is None:
        return None
    minimum = min(field.min_value, value)
    return int(minimum) if isinstance(value, int) else float(minimum)


def render_group_field(field, module, module_code, refs, stored_data, sub_default=None):
    """Рендерит повторяющуюся группу строк (Field.type="group",
    см. core/module_base.py) — таблица виджетов + "Добавить строку"/
    "Удалить" на каждую строку. Возвращает список dict (без служебного
    "_row_id") — то, что попадёт в data[field.name].

    Строки хранятся в session_state ПО СВОЕМУ ключу (`group_rows_...`),
    отдельно от form_data_by_module — та же причина, что и у обычных
    полей (см. комментарий у form_data_by_module выше): виджеты внутри
    ещё не отрисованной группы не переживают переключение модуля.
    Каждая строка получает свой неизменный `_row_id` при создании — не
    позиционный индекс — чтобы ключи виджетов не "плыли" при удалении
    строки из середины списка (иначе Streamlit перепутывает состояние
    виджетов соседних строк после rerun).

    `sub_default` — необязательный колбэк `(row, sub_field) -> значение
    | None`, вызывается ТОЛЬКО пока у этого под-поля этой строки ещё
    нет собственного значения (`sub_stored is None` — то есть только
    на самом первом рендере виджета новой строки; как только оператор
    один раз увидел виджет, Streamlit хранит его состояние по `key=` и
    дальше `sub_default` для этого виджета уже не вызывается — разовый
    дефолт, не постоянная привязка). Получает УЖЕ заполненные к этому
    моменту под-поля ЭТОЙ ЖЕ строки (под-поля рендерятся по порядку
    `group_fields` — например, площадь плана может смотреть на уже
    выбранный в этой же строке масштаб, см. modules/geodesy/module.py
    и вызов ниже про план/съёмку)."""
    rows_key = f"group_rows_{module_code}_{field.name}"
    next_id_key = f"{rows_key}__next_id"

    if rows_key not in st.session_state:
        seeded = [dict(row) for row in (stored_data.get(field.name) or [])]
        st.session_state[next_id_key] = 0
        if not seeded:
            seeded = [{}]
        for row in seeded:
            row["_row_id"] = st.session_state[next_id_key]
            st.session_state[next_id_key] += 1
        st.session_state[rows_key] = seeded

    rows = st.session_state[rows_key]
    st.markdown(f"**{field.label}**" + (" \\*" if field.required else ""))

    row_id_to_remove = None
    for row in rows:
        row_id = row["_row_id"]
        cols = st.columns(len(field.group_fields) + 1)
        for col, sub_field in zip(cols[:-1], field.group_fields):
            sub_key = f"group_{module_code}_{field.name}_{row_id}_{sub_field.name}"
            sub_stored = row.get(sub_field.name)
            computed_default = (
                sub_default(row, sub_field) if sub_default is not None and sub_stored is None else None
            )
            with col:
                if sub_field.dynamic_options:
                    options = module.field_options(sub_field.name, refs)
                    if options:
                        values = [value for value, _label in options]
                        labels_by_value = dict(options)
                        preferred = sub_stored if sub_stored is not None else computed_default
                        index = values.index(preferred) if preferred in values else 0
                        if sub_field.name in row and preferred is None:
                            # Строку прислал черновик из ТЗ без выбора (например, категория
                            # грунта — ввод оператора): не подставлять молча первый вариант
                            # (ревью 03.11, №1) — пусто, пока оператор не выберет.
                            index = None
                        row[sub_field.name] = st.selectbox(
                            sub_field.label, values, index=index,
                            format_func=lambda v, labels=labels_by_value: labels[v], key=sub_key,
                            placeholder="— выберите —",
                        )
                    else:
                        st.selectbox(
                            sub_field.label, [],
                            placeholder="Нет данных в справочнике", disabled=True, key=sub_key,
                        )
                        row[sub_field.name] = None
                elif sub_field.type == "number":
                    raw_value = sub_stored if sub_stored is not None else computed_default
                    if sub_field.integer:
                        # См. комментарий у Field.integer в
                        # core/module_base.py — иначе Streamlit сам
                        # выбирает step=0.01 для float-полей.
                        default_value = int(raw_value) if raw_value is not None else 0
                        row[sub_field.name] = st.number_input(
                            sub_field.label, value=default_value, step=1, key=sub_key,
                            min_value=number_min_value(sub_field, default_value),
                        )
                    else:
                        default_value = float(raw_value) if raw_value is not None else 0.0
                        row[sub_field.name] = st.number_input(
                            sub_field.label, value=default_value, key=sub_key,
                            min_value=number_min_value(sub_field, default_value),
                        )
                else:
                    default_text = sub_stored if sub_stored is not None else (computed_default or "")
                    row[sub_field.name] = st.text_input(
                        sub_field.label, value=default_text, key=sub_key
                    )
        with cols[-1]:
            st.write("")  # выравнивание кнопки по высоте остальных виджетов строки
            if st.button("Удалить", key=f"remove_{module_code}_{field.name}_{row_id}"):
                row_id_to_remove = row_id

    if row_id_to_remove is not None:
        st.session_state[rows_key] = [r for r in rows if r["_row_id"] != row_id_to_remove]
        st.rerun()

    if st.button("Добавить строку", key=f"add_{module_code}_{field.name}"):
        # Новая строка стартует пустой — её под-поля посчитают свои
        # дефолты сами при первом рендере через sub_default (см.
        # докстринг выше), а не заранее одним снимком здесь.
        new_row = {"_row_id": st.session_state[next_id_key]}
        st.session_state[next_id_key] += 1
        st.session_state[rows_key].append(new_row)
        st.rerun()

    return [{k: v for k, v in row.items() if k != "_row_id"} for row in st.session_state[rows_key]]


def render_input_fields(fields, module, module_code, key_code, refs, stored_data, pre_missing):
    """Форма ввода по списку `Field` — общая для одноэтапного сценария
    и для любого этапа многоэтапного. `key_code` — часть ключей
    виджетов: для одноэтапного модуля и первого этапа это просто
    `module_code` (ключи виджетов те же, что были до многоэтапности), для
    более поздних этапов — `<module_code>_<stage_key>`."""
    data = {}
    for field in fields:
        widget_key = f"field_{key_code}_{field.name}"
        label = field_label(field, invalid=field.label in pre_missing)
        hint = split_label(field.label)[1] or None  # длинная часть подписи — в подсказку «?»
        stored_value = stored_data.get(field.name)
        if field.type == "group":
            sub_default = None
            if module_code == "geodesy" and field.name == VK_TOPO_PLANS:
                # Удобный (не жёсткий) дефолт: площадь новой строки плана
                # подтягивается от строки СЪЁМКИ С ТЕМ ЖЕ масштабом и
                # сечением (не от последней введённой съёмки по порядку) —
                # если совпадающих несколько, берём последнюю из
                # совпадающих; если совпадения нет вообще — не гадаем,
                # оставляем 0 для ручного ввода (см. modules/geodesy/module.py,
                # докстринг про съёмку/план). Сечение той же строки плана
                # само подтягивается от съёмки того же масштаба (по тому
                # же принципу) — оно рендерится РАНЬШЕ площади (порядок
                # group_fields), поэтому к моменту расчёта дефолта площади
                # сечение этой строки уже известно.
                def sub_default(row, sub_field):
                    surveys = data.get(VK_TOPO_SURVEYS) or []
                    scale = row.get("topo_scale")
                    if sub_field.name == "topo_section":
                        return match_topo_survey_section(surveys, scale)
                    if sub_field.name == "topo_area":
                        return match_topo_survey_area(surveys, scale, row.get("topo_section"))
                    return None

            data[field.name] = render_group_field(
                field, module, key_code, refs, stored_data, sub_default
            )
            continue
        if field.dynamic_options:
            options = module.field_options(field.name, refs)
            if options:
                values = [value for value, _label in options]
                labels_by_value = dict(options)
                index = values.index(stored_value) if stored_value in values else 0
                # labels=labels_by_value — привязка словаря именно этого поля:
                # без неё lambda видит переменную цикла, и при нескольких
                # полях с dynamic_options подписи берутся от последнего.
                data[field.name] = st.selectbox(
                    label, values, index=index,
                    format_func=lambda v, labels=labels_by_value: labels[v], key=widget_key, help=hint,
                )
            else:
                st.selectbox(
                    label,
                    [],
                    placeholder="Нет данных в справочнике — обратитесь к администратору программы",
                    disabled=True,
                )
                data[field.name] = None
        elif field.type == "number":
            if stored_value is None and field.default is not None:
                stored_value = field.default  # см. core/module_base.py::Field.default
            if field.integer:
                # Явный step=1 + целочисленный value — иначе Streamlit сам
                # выбирает step=0.01/format="%0.2f" для float-полей и
                # оператор видит "30,00" вместо "30" уже на этапе ввода
                # (см. core/module_base.py::Field.integer).
                value = int(stored_value) if stored_value is not None else 0
                data[field.name] = st.number_input(
                    label, value=value, step=1, key=widget_key, help=hint,
                    min_value=number_min_value(field, value),
                )
            else:
                value = float(stored_value) if stored_value is not None else 0.0
                data[field.name] = st.number_input(
                    label, value=value, key=widget_key, help=hint,
                    min_value=number_min_value(field, value),
                )
        else:
            data[field.name] = st.text_input(label, value=stored_value or "", key=widget_key, help=hint)
        render_param_rule(module_code, field, data, widget_key, stored_data)
    return data


def unselected_group_choices(fields, data):
    """«Категория грунта (строка 1)» — строки повторяющихся групп, где выбор из
    справочника пуст, а объём введён: подтверждать такой этап нельзя (иначе объём
    уйдёт в строку без цены)."""
    found = []
    for field in fields:
        if field.type != "group":
            continue
        for number, row in enumerate(data.get(field.name) or [], start=1):
            has_volume = any(isinstance(v, (int, float)) and not isinstance(v, bool) and v
                             for v in row.values())
            for sub in field.group_fields:
                if sub.dynamic_options and has_volume and row.get(sub.name) is None:
                    found.append(f"{sub.label} (строка {number})")
    return found


def render_param_rule(module_code, field, data, widget_key, stored_data):
    """Под параметром с правилом (справочник `data/pravila_parametrov.json`, раздел 1.3
    прогона 03.11.2026) — статус, значение по умолчанию, эталон, источник и вопрос к
    специалисту. Если оператор изменил значение программы — поле «обоснование»: оно
    хранится в вводе этапа (`param_rules.REASONS_KEY`), расчёт его не читает."""
    rule = param_rules.rule(module_code, field.name)
    if rule is None:
        return
    st.caption(param_rules.describe(rule))
    if rule.status == param_rules.STATUS_INPUT:
        return
    if param_rules.same_value(data.get(field.name), rule.default):
        return
    stored = (stored_data.get(param_rules.REASONS_KEY) or {}).get(field.name, "")
    reason = st.text_input(
        f"Обоснование изменения: {rule.title.lower()}",
        value=stored, key=f"{widget_key}__reason",
        help="Почему значение отличается от правила программы (документ, указание специалиста, "
        "условие объекта). Сохраняется с версией сметы.",
    )
    data.setdefault(param_rules.REASONS_KEY, {})[field.name] = reason


def param_assumption_text(module_code, inputs, fields):
    """«3 допущения: …» — параметры раздела, посчитанные по правилам-допущениям
    (значение оператор не менял). Пусто — допущений нет."""
    found = param_rules.assumptions(module_code, inputs, fields)
    if not found:
        return ""
    names = "; ".join(f"{r.title} — {param_rules.default_label(r)}" for r in found)
    return f"{len(found)} {plural(len(found), 'допущение', 'допущения', 'допущений')}: {names}"


def plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def render_param_warnings(module_code, inputs, fields):
    """Предупреждения этапа о параметрах с правилами, требующие действия: файл правил не читается,
    значение изменено без обоснования. Допущения — `param_assumption_note`, это «к сведению»."""
    rules_error = param_rules.load_error()
    if rules_error:
        # Иначе бюджет с испорченным файлом выглядит чище исправного: допущений «ноль».
        st.error("Справочник правил параметров не читается: значения по умолчанию — прежние "
                 f"значения программы, число допущений неизвестно. {rules_error}")
    no_reason = [u.rule.title.lower() for u in param_rules.param_uses(module_code, inputs, fields)
                 if u.kind == param_rules.USE_NO_REASON]
    if no_reason:
        st.warning("Значение изменено без обоснования: " + "; ".join(no_reason)
                   + ". Укажите обоснование под полем — оно сохранится с версией сметы.")


def next_step_text(module_code):
    """Что делать после подтверждения сметы раздела: следующий неподтверждённый раздел или итог."""
    for code, info in MODULES.items():
        session = st.session_state.staged_sessions.get(code)
        last = session.staged.stages[-1].key if session is not None else None
        if code != module_code and (session is None or session.status(last) != STATUS_CONFIRMED):
            return (f"Дальше: следующий раздел — «{info['label']}» (выбор в боковой панели), если он "
                    "есть в объекте; когда разделы готовы — страница «5. Сводный бюджет».")
    return "Дальше: все разделы подтверждены — страница «5. Сводный бюджет»."


def param_assumption_note(module_code, inputs, fields):
    """Текст о допущениях раздела или пусто: что это и что с этим делать оператору."""
    text = param_assumption_text(module_code, inputs, fields)
    if not text:
        return ""
    return (f"Итог раздела содержит {text}. Значения — допущения программы до ответа специалистов. "
            "Подтвердить смету можно — в сводном бюджете будет отметка; знаете правило для объекта — "
            "измените значение и укажите обоснование.")


def comma_number(value):
    """Число для текста на экране — с десятичной запятой (81,95), как суммы и количества."""
    return f"{value:g}".replace(".", ",")


def estimate_rows(estimate):
    has_placement = any(getattr(line, "placement", None) for line in estimate.values())
    return [
        {
            "Наименование": line.name,
            "Ед.": line.unit,
            # Текст, не число — построчно по unit (core/formatting.py),
            # т.к. st.dataframe/column_config форматирует колонку
            # целиком одним правилом, а разные строки этой сметы могут
            # быть в разных по физическому смыслу единицах (метраж,
            # штуки, площадь) одновременно.
            "Кол-во": format_quantity(line.qty, line.unit),
            # Рубли — текстом с разделителями разрядов и копейками
            # (docs/ui_guide.md §5), как и количество выше.
            "Цена, руб.": money_text(line.price),
            "Сумма, руб.": money_text(calculate_amount(line, estimate)),
            # своя строка — помечена как ручная: «своя строка — по прайсу: …»
            "Обоснование": display_basis(line),
            # Только когда в смете есть свои строки оператора (Э7).
            **({"Место в смете": PLACEMENT_LABELS.get(line.placement, "")} if has_placement else {}),
        }
        for line in estimate.values()
    ]


def volume_rows(lines):
    return [
        {
            "№": number,
            "Наименование": line.name,
            "Ед.": line.unit,
            "Объём": format_quantity(line.qty, line.unit),
            "Источник": SOURCE_LABELS.get(line.source, line.source),
            "Основание": line.basis,
        }
        for number, line in enumerate(lines.values(), start=1)
    ]


# Колонки с числами (суммы, объёмы, коэффициенты) — по правому краю (components/DataTable.md);
# числа в смете и ведомости — текстом (money_text / format_quantity), поэтому выравнивание
# задаётся по названию колонки.
NUMERIC_COLUMN_HINTS = (
    "руб", "Кол-во", "Объём", "Цена", "Сумма", "Ставка", "Коэф", "Индекс", "Значение",
    "Метраж", "Отношение", "С индексацией", "НДС", "Итого", "База", "После снижения", "№",
)


def data_table(data, **kwargs):
    """Таблица по дизайн-коду (components/DataTable.md): метки колонок ПРОПИСНЫМИ, числа —
    по правому краю. Сами данные и названия колонок не меняются (по ним читают тесты и
    выгрузка) — только подписи на экране (column_config). Остальное — как у st.dataframe."""
    columns = list(data[0]) if isinstance(data, list) and data and isinstance(data[0], dict) else []
    config = dict(kwargs.pop("column_config", None) or {})
    for name in columns:
        base = config.get(name)
        if base is None:
            base = st.column_config.Column()
        elif isinstance(base, str):
            base = st.column_config.Column(label=base)
        base = dict(base)
        base.setdefault("label", name)
        base["label"] = str(base["label"] or name).upper()
        if any(hint in name for hint in NUMERIC_COLUMN_HINTS) and not base.get("alignment"):
            base["alignment"] = "right"
        config[name] = base
    kwargs.setdefault("placeholder", "—")
    return st.dataframe(data, column_config=config or None, **kwargs)


def render_lines_table(lines):
    """Таблица строк этапа: ведомость (`VolumeLine`) или смета."""
    if lines and isinstance(next(iter(lines.values())), VolumeLine):
        data_table(volume_rows(lines), hide_index=True)
    else:
        data_table(estimate_rows(lines), hide_index=True)


def calc_date_iso():
    """Дата расчёта (поле «Дата расчёта» в боковой панели) в ISO."""
    return st.session_state["calc_date"].isoformat()


def index_policy():
    return INDEX_POLICY_STRICT if st.session_state.get("index_strict") else INDEX_POLICY_LAST_KNOWN


def render_estimate_totals(estimate, module):
    """Итог сметы на дату расчёта (блок результата). Возвращает `TotalWithVat`
    — его же берёт выгрузка в Excel (`render_estimate_download`)."""
    refs = run_context().refs
    totals = calculate_total_with_vat(
        estimate, refs, work_type=module.code, date=calc_date_iso(), index_policy=index_policy()
    )
    # Предупреждения — всегда и сверху итога, не только когда итога нет:
    # индекс прошлого периода (политика «последний известный») даёт итог,
    # но оператор должен видеть, что индекс не на дату расчёта.
    for warning in totals.warnings:
        st.warning(warning)
    # Не "1991 г." — база цен разная у разных модулей (геология/
    # геофизика — 1991, геодезия — 2004), формулировка намеренно
    # не называет год (см. записи разработки).
    if totals.total_with_vat is None:
        st.write(f"Сумма в базовых ценах: {rub(totals.base_total)}")
    if totals.after_index_total:
        st.write(
            "Работы вне сборника после индекса (уже в текущих ценах, не индексируются): "
            f"{rub(totals.after_index_total)}"
        )
    if totals.total_with_vat is not None:
        st.html(theme.figures([
            ("В базовых ценах", money_text(totals.base_total), "руб.", "до индекса и НДС", False),
            ("С индексацией, без НДС", money_text(totals.indexed_total), "руб.",
             f"индекс ×{comma_number(totals.index)}", False),
            ("Итого с НДС", money_text(totals.total_with_vat), "руб.",
             f"НДС {totals.vat_rate:.0%} на {st.session_state['calc_date']:%d.%m.%Y}", True),
        ]))
        period = f", {totals.index_period}" if totals.index_period else ""
        st.write(
            f"Итого с индексацией (×{comma_number(totals.index)}{period}) и НДС "
            f"({totals.vat_rate:.0%}) на {st.session_state['calc_date']:%d.%m.%Y}: "
            f"**{rub(totals.total_with_vat)}**"
        )
        if totals.vat_label:
            st.caption(totals.vat_label)
    else:
        st.write("Итог с индексацией и НДС недоступен — см. предупреждения выше.")
    return totals


def render_estimate_download(estimate, module, totals, volumes=None):
    """Кнопка «Скачать Excel» сметы (второстепенное действие панели).
    `volumes` — строки утверждённой ведомости (второй лист выгрузки)."""
    object_data = run_context().object_data
    name = st.session_state.get("save_smeta_name") or ""
    info = {
        "Смета": name or "без названия",
        "Раздел": MODULES[module.code]["label"],
        "Дата расчёта": f"{st.session_state['calc_date']:%d.%m.%Y}",
        "Политика индекса": "строгая" if index_policy() == INDEX_POLICY_STRICT else "последний известный",
        **object_data.info(),
    }
    buffer = io.BytesIO()
    export_to_excel(
        estimate, buffer, totals=totals, volumes=volumes, info=info, source_labels=SOURCE_LABELS
    )
    st.download_button(
        "Скачать Excel сметы",
        data=buffer.getvalue(),
        file_name=export_file_name(f"Смета_{module.code}", object_data.code or name, calc_date_iso()),
        mime=XLSX_MIME,
        key=f"download_estimate_{module.code}",
    )


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def confirmed_but_outdated(session, stage_key):
    """Этап подтверждён, но расчёт с текущим контекстом (данные объекта,
    справочник, версия программы) даёт другой результат, чем снимок: тогда
    снимок не совпадает с тем, что видит оператор, — в бюджет его не берём."""
    if session.status(stage_key) != STATUS_CONFIRMED:
        return False
    try:
        draft = session.compute(stage_key, run_context().refs)
    except (StageNotReady, KeyError, ValueError):
        return False
    snapshot = session.result(stage_key)
    if fingerprint(draft.effective) == snapshot.fingerprint:
        return False
    # Поменялся только текст основания (новая версия программы уточнила
    # подпись), числа те же — снимок по-прежнему верен, в бюджет берём.
    return numbers_fingerprint(draft.effective) != numbers_fingerprint(snapshot.lines)


def build_budget_from_sessions():
    """Сводный бюджет по многоэтапным сессиям модулей этой вкладки
    браузера: итог последнего этапа каждого модуля на дату расчёта.
    Модуль, у которого последний этап не подтверждён или устарел, в
    бюджет не входит (core/aggregator.py), но перечислен с причиной.
    Подтверждённая смета, которая с текущими данными объекта считается
    иначе (`confirmed_but_outdated`), тоже не входит — как устаревшая.
    Возвращает (aggregator, подписи модулей с подтверждёнными ранними
    этапами, но без сметы, подписи модулей, расчёт которых не начат,
    подписи модулей с разошедшимся снимком)."""
    refs, object_data = run_context().refs, run_context().object_data
    aggregator = BudgetAggregator(
        budget_reduction=object_data.reduction("budget"),
        show_mobilization=object_data.show_mobilization,
    )
    in_progress, not_started, outdated, no_basis = [], [], [], []
    custom_shares = {}
    # Параметры-допущения по справочнику правил (core/param_rules.py) у каждой сметы.
    assumptions = {}
    for code, info in MODULES.items():
        session = st.session_state.staged_sessions.get(code)
        last_key = session.staged.stages[-1].key if session is not None else None
        result = session.result(last_key) if session is not None else None
        if result is None:
            started = session is not None and any(
                session.result(stage.key) is not None for stage in session.staged.stages
            )
            (in_progress if started else not_started).append(info["label"])
            continue
        totals = calculate_total_with_vat(
            dict(result.lines), refs, work_type=code, date=calc_date_iso(),
            index_policy=index_policy(),
        )
        status = session.last_stage_status()
        differs = confirmed_but_outdated(session, last_key)
        if differs:
            outdated.append(info["label"])
        missing_basis = custom_without_basis(result.lines)
        if missing_basis:
            no_basis.append(f"{info['label']} ({', '.join(missing_basis)})")
        share = custom_lines_share(result.lines, totals)
        if share is not None:
            custom_shares[info["label"]] = share
        found = param_rules.assumptions(code, result.inputs, [f.name for f in session.staged.stages[-1].fields])
        if found:
            assumptions[info["label"]] = found
        aggregator.add_module(
            ModuleBudget.from_totals(
                code, info["label"], totals,
                confirmed=status != STATUS_DRAFT,
                stale=status == STATUS_STALE or differs or bool(missing_basis),
                reduction=object_data.reduction(code),
                mobilization_base=mobilization_base(dict(result.lines)),
                notes=missing_accrual_notes(session, last_key),
                assumptions=len(found),
            )
        )
    st.session_state["_budget_assumptions"] = assumptions
    included = [m.module_code for m in aggregator.summary()["modules"]]
    aggregator.object_assumptions = len(param_rules.object_assumptions(object_data, included))
    rules_error = param_rules.load_error()
    if rules_error:
        aggregator.object_notes.append(
            "Справочник правил параметров не читается — число допущений неизвестно, разделы "
            f"считаются на прежних значениях программы ({rules_error}).")
    return aggregator, in_progress, not_started, outdated, no_basis, custom_shares


def missing_accrual_notes(session, stage_key):
    """Предупреждение этапа сметы «начисления не применены» — для сводного бюджета:
    там раздел без начислений иначе выглядит обычным (пустые ставки дают −22…−50 %
    итога раздела; аудит 05.11.2026, С-1). Строка без ставок считается и сейчас —
    посчитать нельзя только при неготовом этапе (тогда раздела в бюджете нет)."""
    try:
        draft = session.compute(stage_key, run_context().refs)
    except (StageNotReady, KeyError, ValueError):
        return []
    return [w for w in draft.warnings if w.startswith(MISSING_ACCRUALS_PREFIX)]


def custom_lines_share(lines, totals):
    """Доля своих строк (работ вне сборника) в итоге раздела с индексацией, без НДС:
    строки «до индекса» — × индекс, «после индекса» — как есть. None — своих строк нет
    или итога с индексацией нет."""
    custom = [line for line in lines.values() if is_custom_id(line.id)]
    if not custom or not totals.indexed_total:
        return None
    amounts = {line.id: calculate_amount(line, lines) for line in custom}
    after = sum(a for line_id, a in amounts.items() if lines[line_id].placement == PLACEMENT_AFTER_INDEX)
    before = sum(amounts.values()) - after
    return (before * (totals.index or 0.0) + after) / totals.indexed_total


def money(value):
    return None if value is None else round(value, 2)


# Колонка своих строк «после индекса» в таблице сводного бюджета (находка ревью Э7:
# без неё База × Индекс ≠ «С индексацией» без объяснения на экране).
BUDGET_AFTER_INDEX_COLUMN = "Работы вне сборника после индекса"
# «После снижения» — две колонки, как в выгрузке Excel (ревью сводной №2):
# только коэффициент раздела (= «После снижения раздела» листа) и раздел × сводная.
# Главная кнопка сметы, выпавшей из бюджета после обновления программы (В-42).
RECALC_CONFIRM_LABEL = "Пересчитать и подтвердить заново"

# Сколько параметров сметы раздела посчитано по правилам-допущениям (core/param_rules.py).
ASSUMPTIONS_COLUMN = "Допущений в параметрах"
# Заданы ли ставки начислений раздела (пустые ставки сильно занижают итог раздела).
ACCRUALS_COLUMN = "Начисления"

# Сколько процентов итога раздела (с индексацией, без НДС) дают свои строки оператора.
CUSTOM_SHARE_COLUMN = "Своих строк, % раздела"
BUDGET_AFTER_SECTION_COLUMN = "После снижения раздела, без НДС"
BUDGET_AFTER_BOTH_COLUMN = "После снижения раздела и сводной, без НДС"


def render_accrual_breakdown(modules):
    """Расшифровка начислений (С3: районный, внешний транспорт, оргликвидация,
    неблагоприятный период, метрология) по разделам бюджета — из
    подтверждённых смет. Нет начислений ни у одного — подсказка, где их задать."""
    rows = []
    for m in modules:
        session = st.session_state.staged_sessions.get(m.module_code)
        result = session.result(session.staged.stages[-1].key) if session is not None else None
        if result is None:
            continue
        for name, rate, amount in accrual_breakdown(dict(result.lines)):
            rows.append({
                "Раздел": m.module_name, "Начисление": name, "Ставка": f"{rate:.4g}",
                "Сумма в ценах раздела, руб.": money_text(amount),
            })
    if rows:
        with st.expander(f"Начисления по разделам ({len(rows)}) — расшифровка"):
            data_table(rows, hide_index=True)
            st.caption(
                "Суммы — в базовых ценах раздела (до индекса и НДС); входят в «База» таблицы "
                "выше. ИГМИ считает свои начисления на этапе сметы — здесь не показаны."
            )
    else:
        st.caption(
            "Начисления (районный коэффициент, внешний транспорт, организация и ликвидация) "
            "не применены — ставки задаются на странице «1. Объект и ТЗ», блок «Начисления»."
        )


def render_reduction_inputs(aggregator, object_min=None):
    """Блок ввода «Снижение цены» (Э9): коэффициент по разделу (после индекса,
    как ×0,65 у ИГМИ в листе «№1.3_ИГМИ») и коэффициент сводной (к сумме
    разделов, как ×0,8 / ×0,85 в «№1ПС_Сводная»); 1 — без снижения. Значения
    — в данных объекта (`ObjectData.price_reductions`): живут в сессии браузера
    и идут в выгрузку Excel; в базу смет попадают только при следующем
    подтверждении этапа или черновике любого раздела (своей кнопки сохранения
    нет); открытие версии их не меняет (`apply_object_data`). Под целевой ценой —
    одно предупреждение «цена ниже минимума» по минимуму объекта (`object_min` —
    `ui_target.object_minimum`, все разделы бюджета с посчитанной себестоимостью)."""
    with block("Ввод: снижение цены (коэффициенты)"):
        st.caption(
            "Как в сводной смете компании: снижение бывает на двух уровнях — внутри раздела после "
            "индекса (например, ×0,65 у гидрометеорологии) и ко всей сводной (×0,8, ×0,85). "
            "1 — без снижения. НДС считается от суммы после снижения. Норм у снижения нет — "
            "это решение о цене по объекту."
        )
        st.caption(
            "Где хранятся коэффициенты: пока открыта программа (в этой вкладке) и в выгрузке "
            "Excel сводного бюджета. Отдельной кнопки сохранения нет: в базу смет они попадут "
            "вместе с данными объекта при следующем подтверждении сметы (или черновике) любого "
            "раздела. Открытие сохранённой версии раздела их не меняет."
        )
        cols = st.columns(3)
        keys = [*MODULES, "budget"]
        for number, key in enumerate(keys):
            label = MODULES[key]["label"] if key in MODULES else "Вся сводная (к сумме разделов)"
            widget_key = reduction_key(key)
            if widget_key not in st.session_state:
                st.session_state[widget_key] = 1.0
            with cols[number % 3]:
                st.number_input(
                    f"Коэффициент: {label}", min_value=REDUCTION_MIN, max_value=1.0, step=0.01,
                    # у сводной — 4 знака: подобранный под целевую цену коэффициент
                    # (render_target_price) хранится полной точностью, показ — 4 знака
                    format="%.3f" if key in MODULES else "%.4f",
                    key=widget_key, persist_state="session",
                    help=("После индекса, к итогу раздела (включая работы вне сборника)."
                          if key in MODULES else "К сумме разделов после их снижения, до НДС. "
                          "Можно подобрать под целевую цену — поле ниже."),
                )
        render_target_price(aggregator, object_min)
        st.checkbox(
            "Показать «в том числе мобилизация / демобилизация»", key="obj_show_mobilization",
            persist_state="session",
            help="Только показ части итога: внешний транспорт + организация и ликвидация с "
            "районным на них, × индекс × снижение, пополам. Итог не меняется. Правило — из "
            "смет заказчика 2026 г., нормы нет.",
        )


# Подбор коэффициента сводной под целевую цену (Э9): поле целевой цены, сообщение
# последнего подбора (целевая, подобранный k, текст, вид сообщения).
REDUCTION_MIN = 0.01
TARGET_PRICE_KEY = "target_price_with_vat"
TARGET_FIT_KEY = "_target_fit"
TARGET_FIT_BUTTON = "Подобрать коэффициент"
TARGET_ABOVE_ESTIMATE = "Целевая цена выше сметы — подбор не нужен"


def target_above_estimate_text(target, before):
    """Сообщение «подбор не нужен»: целевая цена с НДС не ниже итога с НДС при
    коэффициенте сводной 1; текущий (ручной) коэффициент не трогается."""
    head = (TARGET_ABOVE_ESTIMATE if target > before
            else "Целевая цена равна смете — подбор не нужен")
    return (
        f"{head}: {rub(target)} не ниже итога с НДС без снижения сводной ({rub(before)}); "
        "цену выше сметы коэффициентом не получить. Коэффициент сводной не изменён — "
        "если снижение не нужно, поставьте в поле 1."
    )


def fit_target_price(aggregator):
    """Обработчик кнопки «Подобрать коэффициент» (до отрисовки виджетов
    следующего прогона — поэтому может записать значение в поле коэффициента
    сводной): k = целевая / итог с НДС при k = 1
    (`BudgetAggregator.fit_budget_reduction`)."""
    target = st.session_state.get(TARGET_PRICE_KEY)
    if not target:
        st.session_state[TARGET_FIT_KEY] = (
            None, None, "Введите целевую цену с НДС — коэффициент подбирается под неё.", "warning")
        return
    fit = aggregator.fit_budget_reduction(target)
    if fit.coefficient is None:
        st.session_state[TARGET_FIT_KEY] = (target, None, fit.message, "warning")
        return
    if fit.before is not None and target >= fit.before:
        # Подбирать нечего (цену выше сметы коэффициентом k ≤ 1 не получить):
        # введённый вручную коэффициент сводной остаётся как был (хвост «дальше»).
        st.session_state[TARGET_FIT_KEY] = (
            target, None, target_above_estimate_text(target, fit.before), "info")
        return
    if fit.coefficient < REDUCTION_MIN:
        st.session_state[TARGET_FIT_KEY] = (
            target, None,
            f"Целевая цена {rub(target)} меньше 1 % сметы ({rub(fit.before)}) — коэффициент "
            f"{fit.coefficient:.6f} ниже допустимого {REDUCTION_MIN:g}. Проверьте сумму.",
            "warning")
        return
    st.session_state[reduction_key("budget")] = fit.coefficient
    if fit.message:
        st.session_state[TARGET_FIT_KEY] = (target, fit.coefficient, fit.message, "info")
        return
    st.session_state[TARGET_FIT_KEY] = (
        target, fit.coefficient,
        f"Коэффициент сводной подобран: {fit.coefficient:.4f} (= {rub(target)} / {rub(fit.before)} "
        "— итог с НДС без снижения сводной, снижение разделов уже учтено). В поле показаны 4 знака, "
        "в расчёте — полная точность, как в сводной компании: итог совпадает с целевой ценой.",
        "success")


def render_target_price(aggregator, object_min=None):
    """Целевая цена с НДС и второстепенная кнопка «Подобрать коэффициент»
    (Э9; у компании коэффициент сводной подбирают под цену, которую готов
    заплатить заказчик / цену конкурса). Целевая цена живёт в сессии вкладки и
    идёт в выгрузку Excel (лист «Сведения»); хранится только подобранный
    коэффициент — в поле сводной, как ручной ввод (оператор может поправить).
    Под полем — расхождение итога с целевой при текущем коэффициенте и
    «Работа в убыток на N руб.», если целевая ниже минимума объекта
    (`object_min`, сравнение без НДС — core/target_loss.py)."""
    target_col, button_col = st.columns([2, 1], vertical_alignment="bottom")
    with target_col:
        st.number_input(
            "Целевая цена с НДС, руб. (необязательно)", min_value=0.0, value=None,
            step=100000.0, format="%.2f", key=TARGET_PRICE_KEY, persist_state="session",
            help="Цена, которую готов заплатить заказчик (или цена конкурса). Кнопка рядом "
            "подберёт коэффициент сводной так, чтобы «Итого по объекту с НДС» стало равно ей.",
        )
    with button_col:
        st.button(TARGET_FIT_BUTTON, key="fit_target_price", on_click=fit_target_price,
                  args=(aggregator,), disabled=aggregator.total_before_budget_reduction() is None,
                  help="Коэффициент сводной = целевая цена / итог с НДС без снижения сводной.")
    target = st.session_state.get(TARGET_PRICE_KEY)
    fit = st.session_state.get(TARGET_FIT_KEY)
    k = aggregator.budget_reduction
    # Сообщение подбора — пока не изменились целевая цена и коэффициент.
    if fit and fit[0] == target and (fit[1] is None or fit[1] == k):
        getattr(st, fit[3])(fit[2])
    # Одно предупреждение «цена ниже минимума»: по меньшей из двух цен — целевой и
    # цене по коэффициенту в поле сводной (она идёт на лист «КП»); без целевой — по цене
    # после снижения (core/target_loss.py; связка с минимумом всех разделов).
    ui_target.render_target_loss(aggregator, object_min, target)
    total = (aggregator.summary()["total_with_vat_sum"]
             if aggregator.total_before_budget_reduction() is not None else None)
    if target and total is not None:
        diff = target - total
        if abs(diff) < 0.005:
            st.caption(f"Итого по объекту с НДС при коэффициенте сводной {k:.4f} равно целевой цене.")
        else:
            st.caption(
                f"При коэффициенте сводной {k:.4f} итог с НДС {rub(total)} — "
                f"{'ниже' if diff > 0 else 'выше'} целевой цены на {rub(abs(diff))}"
            )


def render_budget_section():
    """Страница «5. Сводный бюджет» (docs/ui_guide.md): ввод снижения цены;
    блок максимума — подтверждённые и не устаревшие модули (база, индекс,
    работы вне сборника после индекса, текущие цены, коэффициенты снижения,
    НДС, итог на дату расчёта); блок минимума и коэффициента; блок «не
    вошли»; панель действий — главная кнопка «Скачать сводный бюджет (Excel)»."""
    object_data = run_context().object_data
    aggregator, in_progress, not_started, outdated, no_basis, custom_shares = build_budget_from_sessions()
    summary = aggregator.summary()
    if object_data.code or object_data.name:
        st.caption(f"Объект: {' — '.join(v for v in (object_data.code, object_data.name) if v)}")
    for warning in object_data.warnings():
        st.warning(warning)

    # Себестоимость геологии — тем же путём, что страница «4. Минимальный бюджет»
    # (ui_minimum.geology_numbers: сохранённый ввод + виджеты), один расчёт на прогон:
    # минимум геологии на страницах 4 и 5 не может разойтись (ревью стыка, №5).
    from app import ui_minimum

    geology_cost = ui_minimum.geology_numbers()
    object_min = ui_target.object_minimum(aggregator, geology_cost)  # все разделы бюджета
    render_reduction_inputs(aggregator, object_min)

    with block(f"Максимальный бюджет по разделам на {st.session_state['calc_date']:%d.%m.%Y}"):
        st.caption(
            "В бюджет входят разделы с подтверждённой и не устаревшей сметой (последним этапом)."
        )
        if summary["modules"]:
            assumptions = {
                m.module_name: st.session_state.get("_budget_assumptions", {}).get(m.module_name, [])
                for m in summary["modules"]
            }
            total_assumptions = sum(len(v) for v in assumptions.values()) + aggregator.object_assumptions
            # Те же отметки — на листах «Сводный бюджет» и «КП» выгрузки (core/aggregator.py).
            for note in aggregator.reliability_notes():
                st.warning(note)
            for excluded in summary["excluded_from_total"]:
                st.warning(f"{excluded['module_name']}: нет итога с НДС — в сумму не вошёл.")
            k = summary["budget_reduction"]

            def after(value, m, coef=k):
                return None if value is None else value * m.reduction * coef

            rows = []
            for m in summary["modules"]:
                period = f" ({m.index_period})" if m.index_period else ""
                rows.append({
                    "Раздел": m.module_name,
                    "База (в ценах раздела)": money(m.base_total),
                    "Индекс": f"{comma_number(m.index)}{period}" if m.index is not None else "—",
                    BUDGET_AFTER_INDEX_COLUMN: money(m.after_index_total),
                    "С индексацией": money(m.indexed_total),
                    "Коэф. раздела": m.reduction,
                    "Коэф. сводной": k,
                    BUDGET_AFTER_SECTION_COLUMN: money(after(m.indexed_total, m, 1.0)),
                    BUDGET_AFTER_BOTH_COLUMN: money(after(m.indexed_total, m)),
                    "НДС": money(after(m.vat_amount, m)),
                    "Итого с НДС": money(after(m.total_with_vat, m)),
                    ACCRUALS_COLUMN: ("не все заданы" if m.notes else
                                      "свои, этап сметы" if m.module_code == "hydromet" else "заданы"),
                    ASSUMPTIONS_COLUMN: len(assumptions.get(m.module_name, [])),
                    **({CUSTOM_SHARE_COLUMN: None if custom_shares.get(m.module_name) is None
                        else round(custom_shares[m.module_name] * 100, 1)} if custom_shares else {}),
                })
            # ИТОГО — только по разделам, вошедшим в итог (есть итог с НДС): иначе
            # колонки ИТОГО не сходятся между собой (ревью сводной №4).
            priced = [m for m in summary["modules"] if m.total_with_vat is not None]
            rows.append({
                "Раздел": "ИТОГО" if len(priced) == len(summary["modules"])
                else "ИТОГО (по вошедшим в итог)",
                "База (в ценах раздела)": None,
                "Индекс": "",
                BUDGET_AFTER_INDEX_COLUMN: money(sum(m.after_index_total for m in priced)),
                "С индексацией": money(summary["indexed_total_sum"]),
                "Коэф. раздела": None,
                "Коэф. сводной": k,
                BUDGET_AFTER_SECTION_COLUMN: money(summary["sections_total"]),
                BUDGET_AFTER_BOTH_COLUMN: money(summary["reduced_total"]),
                "НДС": money(summary["vat_amount_sum"]),
                "Итого с НДС": money(summary["total_with_vat_sum"]),
                ACCRUALS_COLUMN: "",
                ASSUMPTIONS_COLUMN: total_assumptions,
                **({CUSTOM_SHARE_COLUMN: None} if custom_shares else {}),
            })
            # Числа остаются числами (сортировка, выгрузка), формат — с
            # разделителями разрядов по языку браузера (docs/ui_guide.md §5).
            money_column = st.column_config.NumberColumn(format="localized")
            coef_column = st.column_config.NumberColumn(format="%.3f")
            data_table(
                rows, hide_index=True, key="budget_table",
                column_config={
                    **{name: money_column for name in (
                        "База (в ценах раздела)", BUDGET_AFTER_INDEX_COLUMN, "С индексацией",
                        BUDGET_AFTER_SECTION_COLUMN, BUDGET_AFTER_BOTH_COLUMN, "НДС",
                        "Итого с НДС")},
                    "Коэф. раздела": coef_column,
                    "Коэф. сводной": st.column_config.NumberColumn(format="%.4f"),
                    CUSTOM_SHARE_COLUMN: st.column_config.NumberColumn(format="%.1f %%"),
                },
            )
            if aggregator.has_reduction():
                st.write(
                    f"Разделы с индексацией до снижения: {rub(summary['indexed_total_sum'])}; "
                    f"после снижения разделов: {rub(summary['sections_total'])}; после снижения "
                    f"сводной (×{k:.4f}): {rub(summary['reduced_total'])} без НДС."
                )
            st.html(theme.figures([
                ("Без НДС", money_text(summary["reduced_total"]), "руб.",
                 "после снижения" if aggregator.has_reduction() else "с индексацией", False),
                ("НДС", money_text(summary["vat_amount_sum"]), "руб.", None, False),
                ("Итого по объекту с НДС", money_text(summary["total_with_vat_sum"]), "руб.",
                 f"разделов в бюджете: {len(summary['modules'])}", True),
            ]))
            st.write(f"Итого по объекту с НДС: **{rub(summary['total_with_vat_sum'])}**")
            for vat_label in aggregator.vat_labels():
                st.caption(vat_label)
            if aggregator.show_mobilization:
                half = summary["mobilization_reduced"] / 2
                st.write(
                    f"В том числе мобилизация: {rub(half)}, демобилизация: {rub(half)} (без НДС)."
                )
                st.caption(
                    "Это часть итога выше, а не добавка: внешний транспорт + организация и "
                    "ликвидация с районным коэффициентом на них, × индекс × снижение, пополам. "
                    "Правило — из смет заказчика 2026 г., нормы нет."
                )
            if any(m.after_index_total for m in summary["modules"]):
                st.caption(
                    f"«{BUDGET_AFTER_INDEX_COLUMN}» — свои строки оператора, уже в текущих ценах: "
                    "в «Базу» не входят и не индексируются, но входят в «С индексацией» "
                    "(С индексацией = База × Индекс + работы вне сборника после индекса)."
                )
            st.caption(
                "База разделов — в разных ценах (1991, 2004, 2000 г.), их сумма не показывается: "
                "складываются только суммы после индексации."
            )
            render_accrual_breakdown(summary["modules"])
        else:
            st.info("Ни у одного раздела смета ещё не подтверждена — бюджет пуст.")

    if summary["not_confirmed"] or in_progress or outdated or not_started or no_basis:
        with block("Не вошли в бюджет"):
            for label in no_basis:
                st.warning(
                    f"{label}: у своих строк нет основания — в бюджет не вошёл. Заполните основание "
                    "на странице «3. Максимальный бюджет» (блок «Свои строки») и подтвердите смету."
                )
            for label in outdated:
                st.warning(
                    f"{label}: подтверждённая смета не совпадает с текущим расчётом (изменились "
                    "данные объекта, справочник или программа) — в бюджет не вошёл, подтвердите "
                    "смету заново."
                )
            for item in summary["not_confirmed"]:
                # причина уже названа выше (устарела или своя строка без основания) — не дублируем
                if any(label.startswith(item["module_name"]) for label in outdated + no_basis):
                    continue
                st.info(f"{item['module_name']}: {item['reason']} — в бюджет не вошёл.")
            for label in in_progress:
                st.info(f"{label}: смета ещё не подтверждена — в бюджет не вошёл.")
            if not_started:
                st.caption("Расчёт не начат: " + ", ".join(not_started))

    with block("Минимальный бюджет и коэффициент снижения"):
        render_minimum_summary(geology_cost)

    with action_panel():
        blockers = [] if summary["modules"] else ["ни одна смета раздела ещё не подтверждена"]
        info = {
            "Дата расчёта": f"{st.session_state['calc_date']:%d.%m.%Y}",
            "Политика индекса": "строгая" if index_policy() == INDEX_POLICY_STRICT else "последний известный",
            **object_data.info(),
        }
        if st.session_state.get(TARGET_PRICE_KEY):
            info["Целевая цена с НДС (для подбора коэффициента сводной)"] = round(
                float(st.session_state[TARGET_PRICE_KEY]), 2)
        buffer = io.BytesIO()
        loss = ui_target.loss_warning(aggregator, object_min, st.session_state.get(TARGET_PRICE_KEY))
        if summary["modules"]:
            aggregator.to_excel(buffer, info=info, kp_warnings=[loss] if loss else None)
        # Как primary_action: пояснение слева, кнопка справа, причина недоступности — под ними.
        text, button = st.columns([3, 2], vertical_alignment="center")
        text.caption(
            "Файл Excel: строка на каждый раздел, итог, на листе «Сведения» — дата расчёта, "
            "индексы, ставка НДС и данные объекта; при снижении — до снижения, коэффициенты, "
            "после снижения и НДС от суммы после снижения. Лист «КП» — расчёт цены "
            "предложения по разделам (до снижения, коэффициент, цена без НДС, НДС, с НДС)."
        )
        with button:
            st.download_button(
                "Скачать сводный бюджет (Excel)",
                data=buffer.getvalue(),
                file_name=export_file_name("Сводный_бюджет", object_data.code or None, calc_date_iso()),
                mime=XLSX_MIME,
                key="download_budget",
                type="primary",
                disabled=bool(blockers),
            )
        if blockers:
            st.caption(":orange[! Сейчас недоступно: " + "; ".join(blockers) + ".]")


# Коэффициент снижения рядом с практикой компании (С4): без сумм заказчика.
REDUCTION_NOTE = (
    "Как сейчас у компании: снижение цены подбирается под целевую цену — коэффициент = "
    "целевая цена / смета по сборникам, без проверки, что цена остаётся выше затрат. "
    "Программа выводит коэффициент из двух границ: себестоимость (минимум) / смета по "
    "сборникам с индексацией без НДС (максимум) — ниже него работа в убыток."
)


# Поля ввода расчёта себестоимости: (поле CostInputs, подпись, вид).
# Вид: "int" — целое ≥ 0 (по умолчанию 0); "float" — дробное ≥ 0 (0);
# "optional" — дробное без значения по умолчанию (пусто = не задано).
COST_FIELDS = [
    ("brigades", "Буровых бригад", "int"),
    ("itr", "ИТР в поле, чел.", "int"),
    ("drivers", "Водителей, чел.", "int"),
    ("cooks", "Поваров, чел.", "int"),
    ("office_staff", "Камеральная группа, чел.", "int"),
    ("office_months", "Камеральные работы, мес", "float"),
    ("salary_worker", "Оклад бурового рабочего, руб./мес", "optional"),
    ("salary_itr", "Оклад ИТР, руб./мес", "optional"),
    ("salary_driver", "Оклад водителя, руб./мес", "optional"),
    ("salary_cook", "Оклад повара, руб./мес", "optional"),
    ("salary_office", "Оклад камеральщика, руб./мес", "optional"),
    ("mileage_uaz", "Пробег УАЗ, км", "float"),
    ("mileage_kamaz", "Пробег КАМАЗ, км", "float"),
    ("mileage_zil", "Пробег ЗИЛ, км", "float"),
    ("mileage_ural", "Пробег Урал, км", "float"),
    ("fuel_price_ai92", "Цена АИ-92, руб./л", "optional"),
    ("fuel_price_diesel", "Цена дизеля, руб./л", "optional"),
    ("lowboy_units", "Единиц техники на трале (пусто — по числу бригад)", "optional"),
    ("lab_samples", "Образцов в сторонние лаборатории", "int"),
    ("contingency_rate", "Резерв непредвиденных, доля (пусто — норма 0,0852)", "optional"),
    ("depreciation", "Амортизация, руб.", "float"),
    ("business_trip", "Командировочные, руб.", "float"),
]


def cost_widget_value(kind, value):
    if kind == "int":
        return int(value or 0)
    if kind == "float":
        return float(value or 0.0)
    return None if value is None else float(value)


def apply_cost_inputs(data):
    """Сохранённый ввод расчёта себестоимости — в виджеты (до отрисовки).
    `data=None` — сброс к пустому вводу (у открытой версии расчёта нет)."""
    inputs = CostInputs.from_dict(data)
    st.session_state["cost_inputs_geology"] = inputs.to_dict()
    for name, _label, kind in COST_FIELDS:
        st.session_state[f"cost_{name}"] = cost_widget_value(kind, getattr(inputs, name))


def geology_drilling_meters(session):
    """Метраж бурения по утверждённой ведомости геологии или None, если
    ведомость не подтверждена (себестоимость без неё не считается)."""
    volumes = session.result("volumes")
    if volumes is None or session.status("volumes") != STATUS_CONFIRMED:
        return None
    return sum(
        line.qty for key, line in volumes.lines.items() if key.startswith(DRILLING_VOLUME_KEY_PREFIX)
    )


def geology_cost_numbers(session, meters, values):
    """Себестоимость (вариант Б) и три числа «максимум / минимум /
    коэффициент» по вводу `values` (словарь полей CostInputs). Общая для
    страницы «Минимальный бюджет» и сводки на странице «Сводный бюджет»."""
    refs, object_data = run_context().refs, run_context().object_data
    values = dict(values)
    if values.get("lowboy_units") is not None:
        values["lowboy_units"] = int(values["lowboy_units"])
    inputs = CostInputs.from_dict(values)
    try:
        # нормы геологии вместе с общими ставками (core/cost.py, трек 3 03.11.2026)
        norms = load_cost_norms(NORMS_NAME)
    except (OSError, ValueError) as exc:
        # битый локальный слой компании — работают допущения, страница не падает (С8)
        st.warning(f"Локальный слой норм компании не прочитан, работают допущения: {exc}")
        norms = load_cost_norms(NORMS_NAME, use_local=False)
    from app import ui_minimum  # общие затраты объекта (страница «4. Минимальный бюджет»)

    result = compute_cost(inputs, meters, distance_km=object_data.distance_km, norms=norms,
                          shared=ui_minimum.shared_costs())

    smeta_max = None
    if session.is_ready("estimate"):
        estimate = session.compute("estimate", refs).effective
        totals = calculate_total_with_vat(
            estimate, refs, work_type="geology", date=calc_date_iso(), index_policy=index_policy()
        )
        smeta_max = totals.indexed_total
    return inputs, result, reduction(result, smeta_max)


def geology_rates_status(inputs):
    """Откуда ключевые ставки себестоимости (`cost.rates_status`): от
    бухгалтерии / цены / допущения / ввод. Битый локальный слой — как в
    расчёте: работают допущения (предупреждение уже показал расчёт)."""
    try:
        norms, local_inputs = load_cost_norms(NORMS_NAME), cost_input_defaults(NORMS_NAME)
    except (OSError, ValueError):
        norms, local_inputs = load_cost_norms(NORMS_NAME, use_local=False), {}
    return rates_status(inputs, norms, local_inputs)


def render_rates_status(status):
    """Плашка калибровки: ставки от бухгалтерии — справка (и всё равно «не
    калиброван по факту»), иначе — предупреждение «допущения / цены»."""
    (st.info if status.from_accounting else st.warning)(status.text())


def render_cost_structure(result):
    """Таблица статей себестоимости: сумма, доля, тип (постоянная / от объёма)."""
    data_table(
        [
            {"Статья": a.article,
             "Сумма, руб.": None if line.amount is None else round(a.amount, 2),
             "Доля, %": round(a.share * 100, 1) if result.cost else None,
             "Тип": a.kind, "Расчёт": line.basis}
            for a, line in zip(cost_structure(result), result.lines)
        ],
        hide_index=True,
        column_config={"Сумма, руб.": st.column_config.NumberColumn(format="localized")},
    )


def geology_session():
    """Сессия этапов геологии (создаётся пустой, если расчёт не начат)."""
    return st.session_state.staged_sessions.setdefault(
        "geology", StagedSession(as_staged(MODULES["geology"]["class"]()))
    )


def render_geology_cost_section(session):
    """Страница «4. Минимальный бюджет» — себестоимость геологии рядом со
    сметой по СБЦ (вариант Б, modules/geology/cost.py) + контроль по архиву
    (вариант А). Расчёт сметы не трогает. Каркас — docs/ui_guide.md: ввод →
    результат → контроль по архиву → панель действий («Сохранить расчёт»)."""
    store = run_context().store
    flash = st.session_state.pop("flash_cost", None)
    if flash is not None:
        (st.success if flash[0] == "ok" else st.error)(flash[1])
    st.subheader("Геология (ОИГ)")
    meters = geology_drilling_meters(session)
    if meters is None:
        st.info("Расчёт себестоимости — после подтверждения ведомости объёмов (нужен метраж бурения).")
        with action_panel():
            primary_action(
                "Сохранить расчёт себестоимости", "save_cost_geology",
                "Расчёт сохраняется при версии сметы геологии.",
                ["не подтверждена ведомость объёмов геологии (нужен метраж бурения)"],
            )
            st.page_link(page_path(PAGE_VOLUMES), label="Перейти: 2. Ведомость объёмов",
                         icon=":material/arrow_forward:")
        return
    # Источник истины — словарь cost_inputs_geology (как form_data_by_module):
    # виджеты, не отрисованные на прогоне (другой модуль, возврат на
    # ведомость), Streamlit удаляет вместе с состоянием. Значение — в
    # session_state до виджета, value= не передаётся (иначе Streamlit
    # предупреждает о двух источниках значения).
    stored = st.session_state.setdefault("cost_inputs_geology", {})
    try:
        local_defaults = cost_input_defaults("geology_cost")
    except (OSError, ValueError) as exc:
        st.warning(f"Локальный слой норм компании не прочитан, работают допущения: {exc}")
        local_defaults = {}
    values = {}
    with block("Ввод: состав партии, сроки, оклады, транспорт"):
        st.caption(
            f"Метраж бурения по утверждённой ведомости: {meters:g} п.м. (погонных метров). "
            "Нормы — допущения программы и ставки компании "
            "этого компьютера, если они внесены; источник каждой нормы — на "
            "странице «Справочники». "
            + (f"Оклады по умолчанию — из локального слоя ({len(local_defaults)}, источник — в «?» "
               "у поля), их можно изменить." if local_defaults else
               "Оклады и цены топлива — только ввод, без значений по умолчанию (локального слоя "
               "ставок компании на этом ПК нет).")
        )
        cols = st.columns(3)
        for number, (name, label, kind) in enumerate(COST_FIELDS):
            key = f"cost_{name}"
            local = local_defaults.get(name)
            if key not in st.session_state:
                value = stored.get(name)
                if value is None and name not in stored and local is not None:
                    value = local.value
                st.session_state[key] = cost_widget_value(kind, value)
            help_text = " ".join(t for t in (
                ui_help.field_help("geology_cost", name),
                f"По умолчанию — локально: {local.source}" if local is not None else None,
            ) if t) or None
            with cols[number % 3]:
                if kind == "int":
                    values[name] = st.number_input(label, min_value=0, step=1, key=key, help=help_text)
                elif kind == "float":
                    values[name] = st.number_input(label, min_value=0.0, key=key, help=help_text)
                else:
                    values[name] = st.number_input(
                        label, min_value=0.0, value=None, key=key, help=help_text
                    )
    st.session_state["cost_inputs_geology"] = dict(values)
    inputs, result, red = geology_cost_numbers(session, meters, values)

    status = geology_rates_status(inputs)

    with block("Результат"):
        if red.warnings:
            # при «минимум > максимума» — одно предупреждение с объяснением по статьям
            st.warning("\n\n".join(red.warnings))
        if result.missing:
            st.info("Не заполнено: " + ", ".join(result.missing))
        cols = st.columns(3)
        cols[0].metric("Максимум: смета по СБЦ (без НДС)", money_text(red.maximum))
        cols[1].metric("Минимум: себестоимость", money_text(red.minimum))
        cols[2].metric("Коэффициент снижения", coefficient_text(red))
        st.caption(REDUCTION_NOTE)
        if not red.calibrated:
            render_rates_status(status)
        st.markdown("**Себестоимость по статьям**")
        render_cost_structure(result)
        months = "—" if result.field_months is None else f"{result.field_months:.2f}".replace(".", ",")
        st.write(
            f"Полевые работы: {months} мес, партия {result.field_headcount} чел. Прямые затраты "
            f"{rub(result.direct)}; себестоимость **{rub(result.cost)}**"
            + (f"; 1 п.м. — {rub(result.cost_per_meter)}" if result.cost_per_meter else "")
        )
        fot_text = fot_share_text(result)
        if fot_text:
            st.caption(fot_text)
        st.caption(
            "Тип статьи: «постоянная» — почти не зависит от метража (проезд, камеральная группа, "
            "трал, суммы на объект); «от объёма» — растёт с метражом и сроком поля; «доля от "
            "других статей» — соцотчисления, накладные, налоги (от ФОТ) и резерв."
        )
        for warning in result.warnings:
            st.caption(warning)

    # вариант А — контроль по архиву
    try:
        archive, archive_source = load_archive()
    except FileNotFoundError:
        st.caption("Архив себестоимости к этой сборке не подключён — контроль по архиву недоступен.")
    except (ValueError, KeyError) as exc:
        st.warning(f"Архив себестоимости не прочитан: {exc}")
    else:
        year = st.session_state["calc_date"].year
        with block("Контроль по архиву (вариант А)"):
            if is_demo_archive(archive_source):
                st.info(DEMO_ARCHIVE_NOTE)
            st.write(archive_summary(archive, year))
            data_table(
                [
                    {"Объект": o.code, "Годы": o.years, "Метраж, п.м.": o.meters,
                     "Факт 1 п.м., руб.": o.fact_cost_per_m, "Норматив 1 п.м., руб.": o.norm_cost_per_m,
                     "Отношение": o.ratio, "Примечание": o.note or ""}
                    for o in archive_by_proximity(archive, year)
                ],
                hide_index=True,
            )
            st.caption(f"{archive_disclaimer(archive, year)} Источник: {archive_source}.")

    with action_panel():
        blockers = [] if session.version_id is not None else [
            "ведомость не сохранена версией — подтвердите ведомость"
        ]
        if primary_action(
            "Сохранить расчёт себестоимости", "save_cost_geology",
            "Ввод и итоги сохраняются при последней версии сметы геологии "
            f"(#{session.version_id})." if session.version_id is not None else
            "Ввод и итоги сохраняются при версии сметы геологии.",
            blockers,
        ):
            summary = {
                "meters": meters, "cost": result.cost, "complete": result.complete,
                "missing": list(result.missing), "fot": result.fot, "direct": result.direct,
                "maximum": red.maximum, "coefficient": red.coefficient,
                "calc_date": calc_date_iso(),
            }
            try:
                from app import ui_minimum

                store.save_cost_calc(session.version_id, "geology",
                                     {**inputs.to_dict(), "_shared": ui_minimum.shared_costs().to_dict()},
                                     summary)
                st.session_state["flash_cost"] = (
                    "ok", f"Расчёт себестоимости сохранён при версии #{session.version_id}.")
            except SmetaStoreBusyError as exc:
                st.session_state["flash_cost"] = ("error", f"Не сохранено: {exc}")
            st.rerun()


def render_overrides_block(lines, overrides, form_prefix, undo_key=None, editable_for=None):
    """Правки оператора, undo и история правок для набора строк.

    `editable_for(line)` — какие поля строки можно править (см.
    core/stages.py::Stage.editable_fields); `None` — по виду строки
    (`core/stages.py::default_editable_fields`: у строки «доля от базы»
    kind B/C — ставка, у остальных — объём и цена). Строки без правимых
    полей формы не получают."""
    st.caption(
        "Если число в строке нужно исправить вручную — раскройте строку, введите значение и "
        "причину. Каждая правка записывается в историю и отменяется кнопкой ниже."
    )

    if st.button("Отменить последнюю правку", disabled=not overrides.history(), key=undo_key):
        overrides.undo()
        st.rerun()

    for line_id, line in lines.items():
        fields = editable_for(line) if editable_for is not None else default_editable_fields(line)
        if not fields:
            continue
        with st.expander(line.name):
            if "qty" not in fields and "price" in fields:
                st.caption(
                    "Объём взят из утверждённой ведомости — правится только "
                    "возвратом на этап ведомости."
                )
            if "rate" in fields:
                st.caption(
                    "Строка считается долей от суммы других строк сметы — правится "
                    "ставка (доля, например 0,0875 = 8,75 %), не объём и цена."
                )
            with st.form(key=f"{form_prefix}_{line_id}"):
                new_qty = new_price = new_rate = None
                if "qty" in fields:
                    # int(line.qty) ТОЛЬКО когда значение и так уже целое —
                    # иначе "проба"/"опред." и т.п. (декларативно целые
                    # единицы, но у них есть строки с ДЕЙСТВИТЕЛЬНО дробным
                    # qty, посчитанным по формуле класса Б/В, например
                    # 74.4 — см. core/formatting.py) молча обрезались бы
                    # до 74 при открытии формы, даже если оператор её не
                    # трогал — это подменило бы посчитанное значение без
                    # намерения оператора (core/overrides.py увидел бы
                    # "изменение" 74.4 -> 74 просто от открытия формы).
                    if decimals_for_unit(line.unit) == 0 and float(line.qty).is_integer():
                        new_qty = st.number_input("Кол-во", value=int(line.qty), step=1)
                    else:
                        new_qty = st.number_input("Кол-во", value=float(line.qty))
                # Нижняя граница 0, если значение строки не отрицательное уже (старые данные).
                if "price" in fields:
                    new_price = st.number_input(
                        "Цена", value=float(line.price), min_value=min(0.0, float(line.price)))
                old_rate = line.coefs.get("rate") if "rate" in fields else None
                if old_rate is not None:
                    new_rate = st.number_input(
                        "Ставка (доля от базы)", value=float(old_rate), step=0.0001, format="%.4f",
                        min_value=min(0.0, float(old_rate)),
                    )
                reason = st.text_input("Причина правки (необязательно)")
                submitted = st.form_submit_button("Применить правку")
                if submitted:
                    if new_qty is not None and new_qty != line.qty:
                        overrides.apply(line_id, "qty", line.qty, new_qty, reason or None)
                    if new_price is not None and new_price != line.price:
                        overrides.apply(line_id, "price", line.price, new_price, reason or None)
                    if new_rate is not None and new_rate != old_rate:
                        overrides.apply(line_id, "rate", old_rate, new_rate, reason or None)
                    st.rerun()

    st.markdown("**История правок**")
    render_edits_table(overrides.history(), empty_text="Правок пока нет.")


EDIT_FIELD_LABELS = {
    FIELD_CUSTOM: "своя строка (добавлена / изменена; сумма)",
    FIELD_CUSTOM_DELETE: "своя строка удалена (сумма)",
}


CUSTOM_LINES_TITLE = "Свои строки (работы вне сборника)"
PLACEMENT_OPTIONS = (PLACEMENT_BEFORE_INDEX, PLACEMENT_AFTER_INDEX)
BASIS_KIND_OPTIONS = tuple(BASIS_KIND_LABELS)


def custom_line_form(form_key, submit_label, current=None):
    """Поля своей строки в форме. Возвращает (нажата ли кнопка, данные, причина)."""
    current = current or {}
    # Не очищать при отправке: при ошибке проверки ввод должен остаться
    # (ревью Э7, №4); после успешного добавления форма получает новый ключ.
    with st.form(key=form_key, clear_on_submit=False):
        name = st.text_input("Наименование работы *", value=current.get("name", ""), key=f"{form_key}_name")
        cols = st.columns(3)
        unit = cols[0].text_input("Единица *", value=current.get("unit", "шт."), key=f"{form_key}_unit")
        qty = cols[1].number_input(
            "Количество *", value=float(current.get("qty", 1.0)), min_value=0.0,
            key=f"{form_key}_qty",
        )
        price = cols[2].number_input(
            "Цена за единицу, руб. *", value=float(current.get("price", 0.0)), min_value=0.0,
            key=f"{form_key}_price",
        )
        basis_kind = st.radio(
            "Вид основания *", BASIS_KIND_OPTIONS,
            index=BASIS_KIND_OPTIONS.index(current.get("basis_kind", BASIS_PRICE_LIST)),
            format_func=lambda key: BASIS_KIND_LABELS[key], horizontal=True,
            key=f"{form_key}_basis_kind",
        )
        basis = st.text_input(
            "Основание * (чей прайс и от какой даты, номер калькуляции, какой объект-аналог)",
            value=current.get("basis", ""), key=f"{form_key}_basis",
        )
        placement = st.radio(
            "Место в смете *", PLACEMENT_OPTIONS,
            index=PLACEMENT_OPTIONS.index(current.get("placement", PLACEMENT_AFTER_INDEX)),
            format_func=lambda key: PLACEMENT_LABELS[key], key=f"{form_key}_placement",
            help="«До индекса» — цена в базовых ценах сборника, умножается на индекс, как строки "
            "модуля (но в базу районного коэффициента и других начислений не входит). «После "
            "индекса» — цена уже в текущих рублях (прайс 2026 г.): прибавляется к итогу после "
            "индексации, как блок «Работы, не учтённые СБЦ-99» в смете ИЭИ. НДС — на всё.",
        )
        reason = st.text_input("Комментарий для истории правок (необязательно)", key=f"{form_key}_reason")
        st.caption(REQUIRED_NOTE)
        submitted = st.form_submit_button(submit_label)
    data = {
        "name": name, "unit": unit, "qty": qty, "price": price,
        "basis_kind": basis_kind, "basis": basis, "placement": placement,
    }
    return submitted, data, reason or None


def render_custom_lines_block(overrides, key_prefix):
    """Блок «Свои строки (работы вне сборника)» этапа сметы (Э7, Методика
    п. 6). Строки — записи журнала правок `overrides` (core/overrides.py):
    сохраняются с версией и черновиком, отменяются «Отменить последнюю
    правку». Кнопки здесь второстепенные (главная на странице — «Подтвердить
    смету», docs/ui_guide.md §3)."""
    with block(CUSTOM_LINES_TITLE):
        st.caption(
            "Работа, которой нет в сборнике цен, оценивается по прайсу, калькуляции или аналогу "
            "(Методика, п. 6). Основание обязательно. Своя строка не меняет расчёт раздела и "
            "не входит в базу начислений (районный коэффициент и т.п.)."
        )
        messages = st.container()
        lines = overrides.custom_lines()
        for data in lines.values():
            if not str(data.get("basis") or "").strip():
                messages.warning(
                    f"Своя строка «{data.get('name')}» без основания (сохранена до того, как "
                    "основание стало обязательным). Заполните основание — «Изменить или удалить» "
                    "ниже; до этого смета не подтверждается и не идёт в сводный бюджет."
                )
        if lines:
            data_table(
                [
                    {
                        "Наименование": data["name"],
                        "Ед.": data["unit"],
                        "Кол-во": format_quantity(data["qty"], data["unit"]),
                        "Цена, руб.": money_text(data["price"]),
                        "Сумма, руб.": money_text(data["qty"] * data["price"]),
                        "Основание": f"{BASIS_KIND_LABELS.get(data.get('basis_kind'), 'основание')}: "
                        f"{str(data.get('basis') or '').strip() or 'не указано'}",
                        "Место в смете": PLACEMENT_LABELS[data["placement"]],
                    }
                    for data in lines.values()
                ],
                hide_index=True,
            )
        else:
            st.write("Своих строк пока нет.")

        with st.expander("Добавить строку", expanded=not lines):
            submitted, data, reason = custom_line_form(
                f"{key_prefix}_add_{len(overrides.history())}", "Добавить строку"
            )
            if submitted:
                try:
                    overrides.add_custom_line(data, reason)
                except ValueError as exc:
                    messages.error(str(exc))
                else:
                    st.rerun()

        for line_id, current in lines.items():
            number = line_id.split(".", 1)[-1]
            with st.expander(f"Изменить или удалить: {current['name']} (своя строка № {number})"):
                # Номер записи журнала в ключе формы: после любой правки
                # (в т.ч. отмены) поля формы заново заполняются из строки.
                submitted, data, reason = custom_line_form(
                    f"{key_prefix}_edit_{line_id}_{len(overrides.history())}",
                    "Сохранить изменения", current,
                )
                if submitted:
                    try:
                        overrides.update_custom_line(line_id, data, reason)
                    except ValueError as exc:
                        messages.error(str(exc))
                    else:
                        st.rerun()
                st.caption("Удаление можно отменить кнопкой «Отменить последнюю правку» в блоке правки строк.")
                if st.button("Удалить строку", key=f"{key_prefix}_delete_{line_id}"):
                    overrides.remove_custom_line(line_id)
                    st.rerun()


def render_edits_table(edits, empty_text=None):
    if edits:
        st.table(
            [
                {
                    "Строка": edit.line_id,
                    "Поле": EDIT_FIELD_LABELS.get(edit.field, edit.field),
                    "Было": edit.old_value,
                    "Стало": edit.new_value,
                    "Причина": edit.reason or "",
                }
                for edit in edits
            ]
        )
    elif empty_text:
        st.write(empty_text)


def format_timestamp(iso_value):
    """Время подтверждения для оператора — по часам этого компьютера: «30.10.2026 11:03».
    В базе хранится UTC (ISO); без часового пояса — считается UTC."""
    try:
        moment = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
    except ValueError:
        return str(iso_value)[:19].replace("T", " ") + " UTC"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"{moment.astimezone():%d.%m.%Y %H:%M}"


def stage_form_keys(module_code, staged, stage):
    """(ключ в form_data_by_module, key_code для ключей виджетов) этапа.
    Первый этап — те же ключи, что у одноэтапного модуля."""
    if staged.index(stage.key) == 0:
        return module_code, module_code
    return f"{module_code}:{stage.key}", f"{module_code}_{stage.key}"


def reset_form_widgets(key_code, fields):
    """Сброс состояния виджетов формы перед заполнением из открытой
    сметы/версии.

    Групповые поля (см. render_group_field) держат СВОИ строки в
    session_state отдельно от form_data_by_module — без явной очистки
    после "Открыть" оператор увидел бы строки предыдущей сессии.

    Состояние самих виджетов (`field_...`, `group_..._<row_id>_...`)
    тоже нужно убрать: если виджет с таким key уже отрисован в этой
    вкладке, Streamlit берёт значение из его состояния и игнорирует
    `value=` — открытая смета показывала бы в форме прежние значения
    (было и до многоэтапности, найдено тестом открытия старой сметы
    геологии, см. записи разработки)."""
    for field in fields:
        st.session_state.pop(f"field_{key_code}_{field.name}", None)
        if field.type == "group":
            st.session_state.pop(f"group_rows_{key_code}_{field.name}", None)
            prefix = f"group_{key_code}_{field.name}_"
            for key in [k for k in st.session_state.keys() if str(k).startswith(prefix)]:
                del st.session_state[key]


def calculation_name(module_code):
    """Имя расчёта для версий — из поля «Имя сметы для сохранения» (его
    значение в боковой панели), иначе
    «<модуль>, без названия»."""
    return st.session_state.get("save_smeta_name") or f"{MODULES[module_code]['label']}, без названия"


def draft_name(module_code):
    """Имя черновика. Черновик один на пару (модуль, имя) и при повторном
    сохранении перезаписывается — поэтому без введённого имени черновик
    получает явное имя с датой и временем, и это имя подставляется в поле
    «Имя сметы для сохранения»: следующее сохранение того же расчёта
    перезапишет именно его, а не общий «без названия» другого расчёта.
    Поле стоит в боковой панели и на этом прогоне уже отрисовано — менять
    его значение сейчас Streamlit не даёт, поэтому имя передаётся через
    `pending_save_name` и подставляется в поле на следующем прогоне (после
    сохранения черновика всегда следует перерисовка)."""
    name = st.session_state.get("save_smeta_name")
    if not name:
        name = f"{MODULES[module_code]['label']}, черновик {datetime.now():%d.%m.%Y %H:%M}"
        st.session_state["pending_save_name"] = name
    return name


def restore_session_form(module_code, staged, session):
    """После восстановления сессии (версия/черновик): ввод всех этапов —
    в form_data_by_module, состояние виджетов формы — сброшено (см.
    reset_form_widgets)."""
    for stage in staged.stages:
        form_key, key_code = stage_form_keys(module_code, staged, stage)
        st.session_state.form_data_by_module[form_key] = dict(session.inputs[stage.key])
        reset_form_widgets(key_code, stage.fields)


def render_stage_bar(session):
    cols = st.columns(len(session.staged.stages))
    for col, stage in zip(cols, session.staged.stages):
        status = session.status(stage.key)
        marker = "▶ " if stage.key == session.current else ""
        col.markdown(f"{marker}**{stage.number}. {stage.title}** — {STATUS_LABELS[status]}")


def render_staged_module(staged, session, module_code, module):
    """Экран текущего этапа многоэтапного модуля (core/stages.py) по каркасу
    docs/ui_guide.md: статус → утверждённые ранние этапы → ввод → результат →
    панель действий (одна главная кнопка «Подтвердить …») → правка строк.
    Сообщение о последнем действии (`flash_<модуль>`) показывает вызывающая
    страница (`render_stage_page`)."""
    refs, store = run_context().refs, run_context().store
    render_stage_bar(session)

    stage = staged.stage(session.current)
    index = staged.index(stage.key)

    status = session.status(stage.key)
    snapshot = session.result(stage.key)
    if status == STATUS_CONFIRMED:
        st.success(f"Этап подтверждён {format_timestamp(snapshot.confirmed_at)}.")
    elif status == STATUS_STALE:
        st.warning(
            f"Этап был подтверждён {format_timestamp(snapshot.confirmed_at)}, но более "
            "ранний этап с тех пор изменён — подтверждённый результат устарел. Проверьте результат ниже "
            "и подтвердите этап заново."
        )
    elif snapshot is not None:
        st.info(
            f"Этап открыт на доработку. Прежняя сохранённая копия от "
            f"{format_timestamp(snapshot.confirmed_at)} сохранён; более поздние этапы "
            "считаются устаревшими, пока этот этап не будет переподтверждён."
        )

    # Подтверждённые более ранние этапы — только чтение. Утверждённая
    # ведомость — самостоятельный выходной документ по ТЗ заказчика,
    # поэтому на экране сметы она показана целиком, а не только итогом.
    # Возврат к ней — второстепенное действие панели действий ниже.
    for earlier in staged.stages[:index]:
        earlier_snapshot = session.result(earlier.key)
        with block(
            f"Утверждено: {earlier.title} (подтверждено "
            f"{format_timestamp(earlier_snapshot.confirmed_at)}, только чтение)"
        ):
            render_lines_table(earlier_snapshot.lines)

    form_key, key_code = stage_form_keys(module_code, staged, stage)
    stored_data = st.session_state.form_data_by_module.setdefault(form_key, {})

    with block(f"Ввод: этап {stage.number}. {stage.title}"):
        # Сообщения о вводе — сверху блока ввода (docs/ui_guide.md §4); их
        # текст известен только после отрисовки полей — место резервируется.
        input_messages = st.container()
        data = render_input_fields(
            stage.fields, module, module_code, key_code, refs, stored_data, []
        )
        if any(f.required for f in stage.fields):
            st.caption(REQUIRED_NOTE)
    st.session_state.form_data_by_module[form_key] = data
    session.inputs[stage.key] = data

    missing = missing_required_fields(stage.fields, data)
    below_min = below_min_fields(stage.fields, data)
    with input_messages:
        if below_min:
            st.error("Значение меньше допустимого (отрицательное): " + ", ".join(below_min))

    draft = None
    with block("Результат"):
        if missing or below_min:
            st.info("Результат этапа появится, когда будут заполнены обязательные поля.")
        else:
            try:
                draft = session.compute(stage.key, refs)
            except StageNotReady as exc:
                st.warning(str(exc))
                return
        totals = None
        if draft is not None:
            field_names = [f.name for f in stage.fields]
            decisions, notes = split_warnings(draft.warnings)
            for warning in decisions:
                st.warning(warning)
            render_param_warnings(module_code, data, field_names)
            assumption_note = param_assumption_note(module_code, data, field_names)
            if assumption_note:
                notes = [*notes, assumption_note]
            if notes:
                with st.expander(f"К сведению ({len(notes)}): проверьте, если это относится к объекту"):
                    for warning in notes:
                        st.warning(warning)
            if draft.not_applied:
                st.warning(
                    "Правки, которые не применились — строки нет в текущем "
                    "результате или это поле на этом этапе не правится. Они "
                    "сохранены в журнале, ничего не удалено:"
                )
                render_edits_table(draft.not_applied)
            render_lines_table(draft.effective)
            if not isinstance(next(iter(draft.effective.values()), None), VolumeLine):
                totals = render_estimate_totals(draft.effective, module)

    unchanged = (
        draft is not None
        and status == STATUS_CONFIRMED
        and fingerprint(draft.effective) == snapshot.fingerprint
    )
    blockers = []
    if missing:
        blockers.append("не заполнены обязательные поля: "
                        + ", ".join(split_label(label)[0] for label in missing))
    if below_min:
        blockers.append("есть отрицательные значения: "
                        + ", ".join(split_label(label)[0] for label in below_min))
    if unchanged:
        blockers.append("этап уже подтверждён, изменений после подтверждения нет")
    unselected = unselected_group_choices(stage.fields, data)
    if unselected:
        blockers.append("не выбрано: " + ", ".join(unselected))
    no_basis = custom_without_basis(draft.effective) if draft is not None else []
    if no_basis:
        blockers.append("у своих строк нет основания: " + ", ".join(no_basis))
    if (draft is not None and isinstance(next(iter(draft.effective.values()), None), VolumeLine)
            and empty_volume_sheet(draft.effective)):
        blockers.append("в ведомости нет ни одного объёма больше нуля")
    is_last = index == len(staged.stages) - 1
    explanation = (
        "Смета фиксируется версией и идёт в сводный бюджет (п. 5)."
        if is_last else
        f"Ведомость фиксируется версией (только чтение), открывается следующий этап — "
        f"«{staged.stages[index + 1].title}» (п. 3)."
    )

    recalc_label = None
    with action_panel():
        only_texts = (
            draft is not None and status == STATUS_CONFIRMED and not unchanged
            and numbers_fingerprint(draft.effective) == numbers_fingerprint(snapshot.lines)
        )
        if only_texts:
            st.info(
                "После обновления программы изменились только тексты оснований строк, суммы те же — "
                "смета остаётся в сводном бюджете. Подтвердите заново, если хотите сохранить новые тексты."
            )
        elif draft is not None and status == STATUS_CONFIRMED and not unchanged:
            # Правки и свои строки после подтверждения — частое действие
            # оператора; причину называем точно (ревью Э7, находка №2).
            edited = tuple(session.overrides[stage.key].history()) != tuple(snapshot.edits)
            cause = (
                "после подтверждения внесены правки или свои строки"
                if edited else
                "изменились данные объекта, справочник или программа"
            )
            if not edited and totals is not None:
                # Смета выпала из бюджета сама — после обновления программы, справочника
                # или данных объекта (В-42): было/стало по итогу раздела и одна кнопка.
                was = calculate_total_with_vat(
                    dict(snapshot.lines), refs, work_type=module.code, date=calc_date_iso(),
                    index_policy=index_policy(),
                )
                recalc_label = RECALC_CONFIRM_LABEL
                st.warning(
                    "**Смета устарела и не входит в сводный бюджет.** "
                    f"Подтверждённая {format_timestamp(snapshot.confirmed_at)} смета теперь "
                    f"считается иначе: итог раздела с НДС был {rub(was.total_with_vat)}, стал "
                    f"{rub(totals.total_with_vat)} Почему: {cause} — например, обновлённая "
                    "программа считает раздел точнее по эталонному листу. Проверьте строки ниже и "
                    f"нажмите «{RECALC_CONFIRM_LABEL}» — сохранится новая версия, прежняя "
                    "остаётся в «Версиях»."
                )
            elif is_last:
                st.warning(
                    f"Текущий расчёт отличается от подтверждённого ({cause}). На экране — "
                    "текущий расчёт; в сводный бюджет раздел не войдёт, пока смета не подтверждена "
                    "заново."
                )
            else:
                # В бюджет идёт последний этап; на ведомости важнее последствие подтверждения.
                st.warning(
                    f"Текущий расчёт отличается от подтверждённого ({cause}). На экране — "
                    "текущий расчёт. Подтвердите заново, только если изменения нужны: смета "
                    "этого раздела станет «устарела», и её тоже придётся подтвердить."
                )
        if is_last and unchanged:
            st.success("Смета подтверждена — раздел готов для сводного бюджета.")
            st.caption(next_step_text(module_code))
        if primary_action(
            recalc_label or stage.confirm_label, f"confirm_{module_code}_{stage.key}", explanation,
            blockers,
        ):
            result = session.confirm(stage.key, refs)
            # Решение №8: каждое подтверждение — новая версия в цепочке,
            # родитель — последняя сохранённая версия этой сессии.
            name = calculation_name(module_code)
            try:
                session.version_id = store.save_version(
                    name, module_code, session.version_id, result, calc_date=calc_date_iso(),
                    object_data=saved_object_data(),
                )
                st.session_state[f"flash_{module_code}"] = (
                    "ok", f"«{stage.title}» подтверждён(а), сохранено как версия #{session.version_id}.",
                )
            except SmetaStoreBusyError as exc:
                st.session_state[f"flash_{module_code}"] = (
                    "error", f"Этап подтверждён, но версия не сохранена: {exc}",
                )
            # Подтверждённая ведомость — сразу к смете (следующий пункт меню);
            # подтверждённая смета остаётся на своей странице.
            go_to_stage(staged, session)

        secondary_actions_label()
        # Кнопки «Ещё» — одной строкой по своей ширине (в трёх равных колонках длинная
        # подпись «Вернуться к этапу …» обрезалась — ревью оформления 03.11).
        row = st.container(horizontal=True)
        # Черновик — незавершённый ввод ЭТОГО этапа (форма + правки строк)
        # без подтверждения и без версии (SmetaStore.save_draft). Доступен
        # всегда, в том числе с незаполненными обязательными полями; один
        # на пару (модуль, имя) — повторное сохранение перезаписывает.
        with row:
            if st.button("Сохранить черновик", key=f"save_draft_{module_code}"):
                name = draft_name(module_code)
                try:
                    store.save_draft(
                        name, module_code, stage.key, session.inputs[stage.key],
                        session.overrides[stage.key].history(), session.version_id,
                        object_data=saved_object_data(),
                    )
                    st.session_state[f"flash_{module_code}"] = (
                        "ok", f"Черновик «{name}» сохранён (этап «{stage.title}», без подтверждения).",
                    )
                except SmetaStoreBusyError as exc:
                    st.session_state[f"flash_{module_code}"] = ("error", f"Черновик не сохранён: {exc}")
                st.rerun()
        if totals is not None:
            with row:
                volume_snapshot = session.result(staged.stages[0].key) if index > 0 else None
                render_estimate_download(
                    draft.effective, module, totals,
                    volumes=dict(volume_snapshot.lines) if volume_snapshot is not None else None,
                )
        for earlier in staged.stages[:index]:
            with row:
                if st.button(
                    f"Вернуться к этапу «{earlier.title}»", key=f"reopen_{module_code}_{earlier.key}",
                    help="Этап откроется на доработку; этот этап будет помечен «устарел» "
                    "(не удаляется) и потребует переподтверждения.",
                ):
                    session.reopen(earlier.key)
                    go_to_stage(staged, session)

    if draft is not None and session.accepts_custom_lines(stage.key):
        render_custom_lines_block(session.overrides[stage.key], f"custom_{module_code}_{stage.key}")

    if draft is not None:
        with block("Правка строк (необязательно)"):
            render_overrides_block(
                draft.effective,
                session.overrides[stage.key],
                form_prefix=f"edit_form_{module_code}_{stage.key}",
                undo_key=f"undo_{module_code}_{stage.key}",
                # Свои строки правятся в своём блоке выше.
                editable_for=lambda line: (
                    () if is_custom_id(line.id) else session.editable_fields(stage.key, line)
                ),
            )

    if status == STATUS_STALE:
        with st.expander("Прежняя (устаревшая) сохранённая копия этого шага"):
            render_lines_table(snapshot.lines)


def object_widget_value(field, value):
    """Значение поля данных объекта → значение виджета."""
    if field.type == "date":
        return date.fromisoformat(value) if value else None
    if field.type == "bool":
        return bool(value)
    if field.type == "number":
        return float(value) if value is not None else None
    return value or ""


# Поля данных объекта, которые открытие версии/черновика не трогает: решение о
# цене всего объекта (страница «5. Сводный бюджет»), а не данные версии одного
# раздела (ревью сводной №1: иначе открытие любой версии молча сбрасывало снижение).
PRICE_DECISION_FIELDS = ("price_reductions", "show_mobilization")


def current_price_decision():
    """Коэффициенты снижения и флаг мобилизации из виджетов текущей сессии."""
    current = ObjectData.from_dict({
        "price_reductions": {
            key: st.session_state.get(reduction_key(key)) for key in REDUCTION_LABELS
        },
        "show_mobilization": bool(st.session_state.get("obj_show_mobilization")),
    })
    return current.price_reductions, current.show_mobilization


def describe_reductions(reductions):
    if not reductions:
        return "без снижения"
    return ", ".join(f"{REDUCTION_LABELS[k]} ×{v:g}" for k, v in reductions.items())


def price_decision_notice(restored):
    """Сообщение, если в открытой версии было своё снижение / мобилизация и
    оно отличается от текущего на странице 5; иначе None."""
    reductions, mobilization = current_price_decision()
    parts = []
    if restored.price_reductions and restored.price_reductions != reductions:
        parts.append(
            f"в открытой версии было снижение цены ({describe_reductions(restored.price_reductions)}), "
            f"на странице «5. Сводный бюджет» оставлено текущее ({describe_reductions(reductions)})"
        )
    if restored.show_mobilization and not mobilization:
        parts.append(
            "в открытой версии был включён показ мобилизации/демобилизации — на странице 5 "
            "оставлено текущее (выключено)"
        )
    if not parts:
        return None
    return ("Снижение цены — решение по всему объекту, открытие версии раздела его не меняет: "
            + "; ".join(parts) + ".")


def apply_object_data(data):
    """Данные объекта из сохранённой версии/черновика — в виджеты блока
    (до их отрисовки на этом прогоне). Коэффициенты снижения и флаг
    мобилизации (`PRICE_DECISION_FIELDS`) не трогаются — остаются текущие;
    если в версии были свои и отличаются — сообщение на экране."""
    restored = ObjectData.from_dict(data)
    for warning in restored.load_warnings():
        set_open_warning(warning)
    set_open_warning(price_decision_notice(restored))
    for field in OBJECT_FIELDS:
        if field.name in PRICE_DECISION_FIELDS:
            continue
        if field.type == "accrual_table":
            for module in ACCRUAL_MODULES:
                for name in ACCRUAL_RATE_LABELS:
                    st.session_state[accrual_override_key(module, name)] = (
                        restored.accrual_overrides.get(module, {}).get(name)
                    )
            continue
        if field.type == "distance_table":
            for module in TRANSPORT_MODULES:
                st.session_state[transport_distance_key(module)] = (
                    restored.transport_distance_overrides.get(module)
                )
            continue
        if field.type == "tz_table":
            apply_tz_widgets(TzParams.from_dict(restored.tz))
            continue
        st.session_state[f"obj_{field.name}"] = object_widget_value(field, getattr(restored, field.name))


def render_object_block():
    """«Данные объекта» — шаг 0, общий для всех модулей (core/object_data.py),
    страница «Объект и ТЗ». Значения — в session_state по ключам `obj_<поле>`;
    `persist_state="session"` сохраняет их, пока открыты другие страницы
    (данные объекта для расчёта собирает `object_data_from_state`)."""
    with block("Данные объекта (общие для всех разделов)"):
        render_object_fields(BASE_OBJECT_FIELDS)
        render_transport_distance_overrides()
    with block("Начисления по объекту (районный коэффициент, транспорт, организация работ)"):
        st.caption(
            "Ставки — доля от базы (0,5 = 50 %). Пусто — начисление не применяется, смета "
            "считается без него (на этапе сметы будет предупреждение). В подсказке «?» у "
            "каждого поля — значения из эталонных смет компании; какие верны для объекта — решает "
            "сметчик."
        )
        render_object_fields(ACCRUAL_FIELDS, help_by_name=ACCRUAL_RATE_HELP, step=0.01)
        render_accrual_overrides()
    result = object_data_from_state()
    for warning in result.warnings():
        st.warning(warning)
    return result


def reduction_key(key):
    """Виджет коэффициента снижения (страница «5. Сводный бюджет»): `budget` —
    сводной, код модуля — раздела."""
    return f"obj_red_{key}"


def accrual_override_key(module, name):
    return f"obj_acc_{module}_{name}"


def transport_distance_key(module):
    return f"obj_trd_{module}"


def render_transport_distance_overrides():
    """Расстояние для внутреннего транспорта отдельно по разделу — перекрывает
    общее расстояние объекта (в сметах компании разделы одного объекта берут
    разные пункты СБЦ-99 т.4: у каждого раздела свой участок работ)."""
    with st.expander("Расстояние для внутреннего транспорта отдельно по разделу"):
        st.caption(
            "По расстоянию выбирается пункт внутреннего транспорта (СБЦ-99 таблица 4). "
            "Пусто — берётся общее расстояние объекта выше. Заполняйте, если участок работ "
            "раздела находится на другом расстоянии от базы."
        )
        cols = st.columns(len(TRANSPORT_MODULES))
        for col, (module, title) in zip(cols, TRANSPORT_MODULES.items()):
            with col:
                st.number_input(
                    f"{title}: расстояние, км", value=None, min_value=0.0, format="%0.2f",
                    key=transport_distance_key(module), persist_state="session",
                    help="Пусто или 0 — общее расстояние объекта. В основании строки транспорта "
                    "сметы будет указано, какое расстояние взято.",
                )


def render_accrual_overrides():
    """Ставки начислений отдельно по разделу — перекрывают общие ставки выше
    (в эталонах компании на одном объекте ставки разные по разделам; В-25)."""
    with st.expander("Ставки отдельно по разделу (если отличаются от общих)"):
        st.caption(
            "Пусто — берётся общая ставка выше. В подсказке «?» — значение эталонного листа "
            "этого раздела."
        )
        cols = st.columns(len(ACCRUAL_MODULES))
        for col, (module, title) in zip(cols, ACCRUAL_MODULES.items()):
            with col:
                st.markdown(f"**{title}**")
                for name, label in ACCRUAL_RATE_LABELS.items():
                    hint = ACCRUAL_ETALON_HINTS.get(module, {}).get(name)
                    st.number_input(
                        label, value=None, min_value=0.0, step=0.01, format="%.4f",
                        key=accrual_override_key(module, name), persist_state="session",
                        help=(f"В эталоне раздела: {hint:g}." if hint is not None
                              else "В эталоне раздела не начисляется."),
                    )


def render_object_fields(fields, help_by_name=None, step=None):
    """Поля данных объекта в две колонки (ключи `obj_<поле>`)."""
    cols = st.columns(2)
    for number, field in enumerate(fields):
        key = f"obj_{field.name}"
        with cols[number % 2]:
            if field.type == "number":
                st.number_input(
                    field.label, value=None, min_value=0.0, key=key, persist_state="session",
                    step=step, format="%.4f" if step else "%0.2f",
                    help=(help_by_name or {}).get(
                        field.name, ui_help.field_help(
                            "object", field.name,
                            "Пусто — не задано (внутренний транспорт — по пункту эталона).")
                    ),
                )
            elif field.type == "date":
                st.date_input(
                    field.label, value=None, key=key, format="DD.MM.YYYY", persist_state="session",
                    help=ui_help.field_help("object", field.name),
                )
            elif field.type == "bool":
                st.checkbox(field.label, key=key, persist_state="session",
                            help=ui_help.field_help("object", field.name))
            else:
                st.text_input(field.label, key=key, persist_state="session",
                              help=ui_help.field_help("object", field.name))


# --- параметры ТЗ и черновик ведомости из них (core/tz.py, 03.11.2026) ----------
#
# Ввод — блок «Параметры технического задания» страницы «1. Объект и ТЗ» (виджеты
# `obj_tz_<параметр>`, `obj_tz_sec_<раздел>`, persist_state="session", как у данных
# объекта; хранятся в ObjectData.tz). «Предложить ведомость» — второстепенная
# кнопка той же страницы: главное действие страницы остаётся «Перейти к ведомости
# объёмов» (docs/ui_guide.md §3) — предложение необязательно, ТЗ часто не даёт данных.
# Само заполнение — до отрисовки виджетов следующего прогона
# (`apply_pending_tz_proposal`, из process_pending_loads): так можно выбрать раздел
# в боковой панели и перейти на страницу 2, как при открытии версии.

TZ_PERMAFROST_OPTIONS = {"": "не указано", "yes": "есть", "no": "нет"}


def tz_key(name):
    return f"obj_tz_{name}"


def tz_section_key(code):
    return f"obj_tz_sec_{code}"


def tz_from_state() -> TzParams:
    """Параметры ТЗ из виджетов блока (на любой странице — persist_state)."""
    permafrost = st.session_state.get(tz_key("permafrost")) or ""
    data = {
        name: st.session_state.get(tz_key(name))
        for name in TZ_LABELS if name not in ("permafrost", "sections")
    }
    data["permafrost"] = {"yes": True, "no": False}.get(permafrost)
    data["sections"] = [code for code in SECTION_LABELS if st.session_state.get(tz_section_key(code))]
    data["key_volumes"] = ui_tz_keys.key_volumes_from_state(tz_modules())
    return TzParams.from_dict(data)


def tz_modules():
    """Экземпляры модулей разделов изысканий — для полей ключевых объёмов."""
    return {code: MODULES[code]["class"]() for code in SECTION_LABELS if code in MODULES}


def apply_tz_widgets(tz: TzParams):
    """Параметры ТЗ сохранённой версии/черновика — в виджеты блока (до отрисовки)."""
    for name in TZ_LABELS:
        if name in ("permafrost", "sections"):
            continue
        value = getattr(tz, name)
        if name in NUMBER_TZ_FIELDS:
            value = float(value) if value is not None else None
        st.session_state[tz_key(name)] = value if value is not None else (
            None if name in NUMBER_TZ_FIELDS or name in INTEGER_TZ_FIELDS else ""
        )
    st.session_state[tz_key("permafrost")] = {True: "yes", False: "no"}.get(tz.permafrost, "")
    for code in SECTION_LABELS:
        st.session_state[tz_section_key(code)] = code in tz.sections
    ui_tz_keys.apply_key_volume_widgets(tz, tz_modules())


def _tz_number(name, help_text=None, integer=False):
    if integer:
        st.number_input(TZ_LABELS[name], value=None, min_value=0, step=1, key=tz_key(name),
                        persist_state="session", help=help_text)
    else:
        st.number_input(TZ_LABELS[name], value=None, min_value=0.0, format="%0.2f",
                        key=tz_key(name), persist_state="session", help=help_text)


def render_tz_block():
    """Блок «Параметры технического задания» страницы «1. Объект и ТЗ»: что задаёт
    ТЗ заказчика, решения исполнителя (по СП и практике) и как предлагать ведомость
    поверх уже введённого. Возвращает TzParams."""
    with block("Параметры технического задания (ТЗ)"):
        st.caption(
            "ТЗ заказчика объёмов работ не задаёт — только параметры объекта. По ним и по "
            "решениям исполнителя ниже программа может предложить черновик ведомости объёмов "
            "выбранных разделов (кнопка «Предложить ведомость» внизу страницы). Пусто — "
            "не задано: такие позиции останутся вводом на странице «2. Ведомость объёмов»."
        )
        st.markdown("**Из ТЗ**")
        cols = st.columns(2)
        with cols[0]:
            st.selectbox(
                TZ_LABELS["object_kind"], list(OBJECT_KINDS), format_func=OBJECT_KINDS.get,
                key=tz_key("object_kind"), persist_state="session",
                help=ui_help.field_help("tz", "object_kind"),
            )
            _tz_number("length_km", "Протяжённость линейного объекта по ТЗ.")
            _tz_number("area_ha", "Площадь площадного объекта (карьер, площадка), если указана в ТЗ.")
            st.text_input(TZ_LABELS["stage"], key=tz_key("stage"), persist_state="session",
                          help="Как в ТЗ: например, «в один этап», «ПД и РД».")
            st.selectbox(
                TZ_LABELS["survey_scale"], [""] + SURVEY_SCALES,
                format_func=lambda v: v or "не указан", key=tz_key("survey_scale"),
                persist_state="session", help=ui_help.field_help("tz", "survey_scale"),
            )
        with cols[1]:
            _tz_number("survey_section_m", "Сечение рельефа основной съёмки, м (например, 0,5).")
            _tz_number("isso_count", "Мосты и водопропускные трубы, всего.", integer=True)
            st.selectbox(
                TZ_LABELS["permafrost"], list(TZ_PERMAFROST_OPTIONS),
                format_func=TZ_PERMAFROST_OPTIONS.get, key=tz_key("permafrost"),
                persist_state="session",
                help="Многолетнемёрзлые грунты (вечная мерзлота) — нужна термометрия в скважинах.",
            )
            _tz_number("seismicity", "Сейсмичность района по ТЗ, баллов.")
        st.markdown("**Состав разделов изысканий** (какие виды изысканий требует ТЗ)")
        section_cols = st.columns(len(SECTION_LABELS))
        for col, (code, label) in zip(section_cols, SECTION_LABELS.items()):
            with col:
                # Короткая подпись («Геология») — длинная не помещается в колонку и обрезается;
                # полная — в подсказке.
                short = label.split(" (", 1)[1].rstrip(")").capitalize() if " (" in label else label
                st.checkbox(short, key=tz_section_key(code), persist_state="session", help=label)
        st.markdown("**Решения исполнителя** — по СП и практике, задаёт исполнитель (в ТЗ их нет)")
        st.caption(
            "Шаг и глубину выработок, ширину полосы съёмки исполнитель выбирает по сводам правил "
            "(СП — свод правил: СП 446, СП 493) и практике компании; правила компании в программу пока не внесены "
            "Метраж по категориям грунта — на странице ведомости."
        )
        cols = st.columns(2)
        with cols[0]:
            _tz_number("holes_count", "Если задано — шаг выработок не используется.", integer=True)
            _tz_number("hole_spacing_m", "Число скважин = протяжённость / шаг (вверх).")
        with cols[1]:
            _tz_number("hole_depth_m", "Метраж бурения = число скважин × средняя глубина.")
            _tz_number("strip_width_m", "Площадь съёмки, га = протяжённость, км × ширина, м / 10.")
        ui_tz_keys.render_key_volumes(
            tz_modules(), [code for code in SECTION_LABELS if st.session_state.get(tz_section_key(code))])
        st.radio(
            "Если в ведомости раздела уже есть ввод", list(MODE_LABELS), format_func=MODE_LABELS.get,
            key="tz_merge_mode", persist_state="session",
            help="Повторное «Предложить ведомость» не затирает введённое молча: по умолчанию "
            "заполняются только пустые поля; при замене на странице ведомости будет список "
            "заменённых полей.",
        )
    tz = tz_from_state()
    for warning in tz.warnings():
        st.warning(warning)
    return tz


def render_tz_propose_button():
    """«Предложить ведомость» — второстепенная кнопка панели действий страницы 1."""
    tz = tz_from_state()
    reason = None if tz.sections else "отметьте разделы изысканий в блоке ТЗ"
    if st.button(
        "Предложить ведомость", key="tz_propose", disabled=reason is not None,
        help=reason or "Черновик ввода ведомости выбранных разделов по параметрам ТЗ; ничего "
        "не подтверждается, всё правится на странице «2. Ведомость объёмов».",
    ):
        st.session_state["pending_tz_proposal"] = {
            "tz": tz.to_dict(), "mode": st.session_state.get("tz_merge_mode") or MODE_FILL_EMPTY,
            "propose_mode": ui_tz_keys.propose_mode(),
        }
        st.rerun()
    if reason:
        st.caption(f"«Предложить ведомость» сейчас недоступно: {reason}.")


def apply_tz_proposal(tz: TzParams, mode: str, propose_mode: str = PROPOSE_TZ):
    """Заполнить ввод этапа «Ведомость объёмов» выбранных разделов предложением
    модулей (core/tz.py). Возвращает (разделы, в которых ввод заполнен; сообщения
    о пропущенных разделах). Подтверждённую ведомость не трогает."""
    applied, skipped = [], []
    records = st.session_state.setdefault("tz_proposals", {})
    for code in tz.sections:
        module = MODULES[code]["class"]()
        staged = as_staged(module)
        first = staged.stages[0]
        proposal = propose_stage_input(module, tz, propose_mode)
        if proposal is None:
            skipped.append(f"{MODULES[code]['label']}: модуль не умеет предлагать ведомость")
            continue
        session = st.session_state.staged_sessions.setdefault(code, StagedSession(staged))
        if session.current != first.key:
            skipped.append(
                f"{MODULES[code]['label']}: ведомость уже подтверждена — чтобы применить "
                "предложение, вернитесь к ней на странице «2. Ведомость объёмов»"
            )
            continue
        form_key, key_code = stage_form_keys(code, staged, first)
        current = st.session_state.form_data_by_module.get(form_key) or session.inputs.get(first.key)
        # прошлое предложение программы этой же сессии — его можно обновлять (core/tz.py)
        previous = records.get(code)
        previous = previous["inputs"] if previous and previous.get("session") is session else None
        merged = merge_proposal(current, proposal.inputs, mode, previous)
        st.session_state.form_data_by_module[form_key] = merged.inputs
        session.inputs[first.key] = merged.inputs
        reset_form_widgets(key_code, first.fields)
        labels = {f.name: f.label for f in first.fields}
        messages = []
        if merged.filled:
            messages.append("заполнены пустые поля: " + ", ".join(labels[n] for n in merged.filled)
                            + " (0 тоже считается пустым — проверьте, если 0 был нарочно)")
        if merged.kept:
            messages.append(
                "оставлен ваш ввод (предложение другое): " + ", ".join(labels[n] for n in merged.kept)
            )
        if merged.replaced:
            messages.append("заменён ввод: " + ", ".join(labels[n] for n in merged.replaced))
        if merged.updated:
            messages.append("обновлено прошлое предложение программы (вы его не меняли): "
                            + ", ".join(labels[n] for n in merged.updated))
        records[code] = {
            "inputs": proposal.inputs, "sources": proposal.sources, "notes": proposal.notes,
            "messages": messages, "session": session,
        }
        applied.append(code)
    return applied, skipped


def apply_pending_tz_proposal():
    """Нажата «Предложить ведомость» (прошлый прогон): заполнить ввод, выбрать
    раздел и перейти на страницу ведомости. Возвращает страницу или None."""
    pending = st.session_state.pop("pending_tz_proposal", None)
    if pending is None:
        return None
    tz = TzParams.from_dict(pending["tz"])
    applied, skipped = apply_tz_proposal(tz, pending["mode"], pending.get("propose_mode", PROPOSE_TZ))
    st.session_state["tz_proposal_skipped"] = skipped
    if not applied:
        return None
    if st.session_state.get("module_choice") not in applied:
        st.session_state["module_choice"] = applied[0]
    names = ", ".join(MODULES[code]["label"] for code in applied)
    for code in applied:
        st.session_state[f"flash_{code}"] = (
            "ok", f"Черновик ведомости предложен по "
            f"{'ключевым объёмам и ' if pending.get('propose_mode') == PROPOSE_KEY else ''}"
            f"параметрам ТЗ (разделы: {names}). Ничего не "
            "подтверждено — проверьте значения и источник каждого в блоке «Черновик из ТЗ».",
        )
    return PAGE_VOLUMES


def render_tz_skipped():
    """Разделы, к которым предложение не применилось (страница 1 или 2)."""
    for text in st.session_state.pop("tz_proposal_skipped", None) or []:
        st.warning(text)


def render_tz_provenance():
    """Страница «2. Ведомость объёмов»: откуда значения ввода раздела после
    «Предложить ведомость» — из ТЗ, решение исполнителя, правило, оценка, ввод,
    вручную. Только пока ведомость редактируется и сессия та же (открытие версии
    или черновика создаёт новую сессию — старое предложение к ней не относится)."""
    render_tz_skipped()
    module_code, _module, staged = current_module()
    record = st.session_state.get("tz_proposals", {}).get(module_code)
    session = st.session_state.staged_sessions.get(module_code)
    if record is None or session is None or record["session"] is not session:
        return
    first = staged.stages[0]
    if session.current != first.key:
        return
    current = st.session_state.form_data_by_module.get(module_code) or session.inputs.get(first.key)
    with block("Черновик из ТЗ — откуда значения ввода"):
        st.info(
            "Значения ввода ниже предложены по параметрам ТЗ (страница 1). Ничего не "
            "подтверждено: проверьте каждое, особенно «оценка — проверьте»; поля «ввод» "
            "заполните сами. Источник строк ведомости — колонка «Источник» в результате."
        )
        for text in record["messages"]:
            st.warning("При повторном предложении " + text + ".")
        for note in record["notes"]:
            st.caption(note)
        data_table(provenance_rows(first.fields, record, current), hide_index=True)


def saved_object_data():
    """Данные объекта для сохранения с версией/черновиком (None — пусто)."""
    object_data = run_context().object_data
    return None if object_data.is_empty() else object_data.to_dict()


@st.cache_resource
def get_store(db_path):
    """Один SmetaStore на процесс и путь базы (создание таблиц и миграция —
    один раз, а не на каждый прогон скрипта). Путь — ключ кэша: тесты
    подменяют core.persistence.DEFAULT_DB_PATH, и каждой подмене — своё
    хранилище."""
    return SmetaStore(db_path)


def prepare_references():
    """Проверить справочник перед работой (С2): в программе из исходников —
    устарел/повреждён → пересобрать из `reference_data/` (сообщение
    «пересобран: …»); в exe исходников нет — устарел/повреждён →
    `ReferencesError` (внутри exe справочник собирается при сборке, такого
    быть не должно). Актуален → None."""
    if getattr(sys, "frozen", False):
        reason = stale_reason(REFERENCES_DB_PATH)
        if reason is not None:
            raise ReferencesError(
                f"Справочник внутри программы не годится: {reason}. Пересоберите программу "
                "(build_release.bat)."
            )
        return None
    from data.build_references import ensure_references

    return ensure_references(REFERENCES_DB_PATH)


@st.cache_resource
def get_refs():
    """Справочник на процесс (проверка и пересборка — один раз) и сообщение о
    пересборке, если она была."""
    message = prepare_references()
    return ReferenceBook(REFERENCES_DB_PATH), message



# Ошибка открытия сохранённой сметы/версии/черновика (формат старее или
# новее программы, запись удалена) — сообщение вместо падения страницы.
OPEN_ERRORS = (
    SnapshotFormatError, KeyError, TypeError, ValueError, FileNotFoundError, SmetaStoreBusyError,
)


def init_session_state():
    """Состояние сессии браузера, общее для всех страниц меню."""
    # Дата расчёта — по ней берётся индекс пересчёта цен (core/pricing.py).
    # По умолчанию сегодня; сохраняется с каждой версией и подставляется при
    # её открытии. Значение задаётся через session_state, а не value=, —
    # чтобы открытие версии могло его поменять до отрисовки виджета.
    if "calc_date" not in st.session_state:
        st.session_state["calc_date"] = date.today()
    if "overrides_by_module" not in st.session_state:
        st.session_state.overrides_by_module = {}
    if "staged_sessions" not in st.session_state:
        # Состояние многоэтапных модулей (core/stages.py::StagedSession) —
        # одна сессия на модуль, как и form_data_by_module ниже.
        st.session_state.staged_sessions = {}
    if "form_data_by_module" not in st.session_state:
        # Источник истины для введённых значений формы, per-module — НЕ
        # то же самое, что session_state виджетов (`key=f"field_..."`):
        # Streamlit сам удаляет state виджета, если этот виджет не
        # отрисован на данном прогоне скрипта (значит виджеты геологии
        # исчезают из session_state, пока показана геофизика или другая
        # страница меню) — поэтому для переживания переключения модуля и
        # страницы значения нужно хранить отдельно и передавать виджетам
        # явно параметром value=/index=, а не полагаться только на key=
        # (см. записи разработки, задача 2 — это и есть та самая
        # неочевидная развилка). Для более поздних этапов многоэтапного
        # модуля ключ — "<module_code>:<stage_key>" (см. stage_form_keys).
        st.session_state.form_data_by_module = {}


# Виджеты с `persist_state="session"` вне блока данных объекта (те — по префиксу `obj_`).
PERSISTED_WIDGET_KEYS = ("calc_date", "index_strict", "module_choice", "save_smeta_name",
                         TARGET_PRICE_KEY, "tz_merge_mode", ui_tz_keys.MODE_KEY)
STARTED_PAGE_KEY = "_started_page"


def resync_widgets_after_page_change():
    """Первым делом на каждом прогоне (до любой команды Streamlit): если страница
    этого прогона не та, что у предыдущего НАЧАТОГО, — заново отправить в браузер
    значения виджетов, которые живут всю сессию.

    Зачем (аудит 05.11.2026, О-2 — потеря данных объекта). Оператор набирает дату
    расчёта с клавиатуры (календарь остаётся открытым) и сразу щёлкает по пункту
    меню. Браузер шлёт два запроса: переход на страницу 2 и — следом, от закрывшегося
    календаря — новую дату, ещё от имени страницы 1. Сервер начинает страницу 2,
    тут же прерывает её и выполняет страницу 1. Браузер за это время дважды «сменил
    страницу» и сбросил у себя значения полей, а сервер считает, что поля никуда не
    девались, и значений повторно не шлёт: поля на экране пустые, на сервере —
    заполнены. Следующее действие оператора присылает на сервер пустые поля — и
    данные объекта стираются уже по-настоящему.

    Значение, записанное в `st.session_state` до отрисовки виджета, Streamlit
    отправляет браузеру принудительно — этим и пользуемся. При обычном переходе это
    ничего не меняет (значения те же). На прогонах без смены страницы ничего не
    делаем: принудительная отправка стёрла бы текст, который оператор набирает в
    поле в эту секунду."""
    from streamlit.runtime.scriptrunner import get_script_run_ctx

    current = getattr(get_script_run_ctx(), "page_script_hash", None)
    previous = st.session_state.get(STARTED_PAGE_KEY)
    st.session_state[STARTED_PAGE_KEY] = current
    if previous is None or previous == current:
        return
    for key in list(st.session_state.keys()):
        if not (isinstance(key, str) and (key.startswith("obj_") or key in PERSISTED_WIDGET_KEYS)):
            continue
        value = st.session_state[key]
        if value is None or value == "" or value is False:
            continue  # значение по умолчанию: браузер и так покажет его
        st.session_state[key] = value


def report_open_error(what, exc):
    """Ошибку показываем на следующем прогоне (до неё могли успеть
    отрисоваться виджеты), состояние сессии модуля не трогаем."""
    st.session_state["open_error"] = f"Не удалось открыть {what}: {exc}"
    st.rerun()


def process_pending_loads(refs, store):
    """Восстановление сметы/версии/черновика, открытых на странице «Версии».
    Выполняется главным скриптом до отрисовки виджетов (боковой панели и
    страницы), поэтому может менять их значения. Возвращает страницу меню,
    на которую перейти после открытия, или None."""
    # --- черновик ведомости из параметров ТЗ (кнопка на странице «1. Объект и ТЗ») ---
    if "pending_tz_proposal" in st.session_state:
        return apply_pending_tz_proposal()
    # --- смета, сохранённая одноэтапным способом (кнопка «Открыть») ---
    if "pending_load" in st.session_state:
        pending = st.session_state.pop("pending_load")
        loaded_module_code = pending["module_code"]
        st.session_state["module_choice"] = loaded_module_code
        st.session_state.form_data_by_module[loaded_module_code] = dict(pending["input_data"])

        restored_overrides = OverrideLog()
        for edit in pending["edits"]:
            restored_overrides.apply(edit.line_id, edit.field, edit.old_value, edit.new_value, edit.reason)

        loaded_module = MODULES[loaded_module_code]["class"]()
        loaded_staged = as_staged(loaded_module)
        reset_form_widgets(loaded_module_code, loaded_staged.stages[0].fields)

        if loaded_staged.is_single_stage:
            st.session_state.overrides_by_module[loaded_module_code] = restored_overrides
            # пересчитать сразу, не дожидаясь повторного нажатия "Рассчитать" —
            # смета строится заново из восстановленного ввода (build_volumes/
            # build_estimate через адаптер), а не десериализуется напрямую
            # (см. core/persistence.py)
            st.session_state[f"estimate__{loaded_module_code}"] = loaded_staged.stages[0].compute(
                pending["input_data"], {}, refs, {}
            )
        else:
            # Смета, сохранённая одноэтапным способом, для модуля, который
            # теперь многоэтапный: каждое поле ввода — в тот этап, к
            # которому оно теперь относится (у экологии параметры расценки
            # — на этапе сметы), правки — в последний этап (там, где они и
            # были сделаны). Ничего не подтверждено; правки, которые на
            # новом этапе не правятся (объём строки с ведомостью), будут
            # показаны как "не применились", а не удалены.
            loaded_session = StagedSession(loaded_staged)
            for loaded_stage in loaded_staged.stages:
                stage_input = {
                    field.name: pending["input_data"][field.name]
                    for field in loaded_stage.fields
                    if field.name in pending["input_data"]
                }
                loaded_session.inputs[loaded_stage.key] = stage_input
                form_key, key_code = stage_form_keys(loaded_module_code, loaded_staged, loaded_stage)
                st.session_state.form_data_by_module[form_key] = dict(stage_input)
                reset_form_widgets(key_code, loaded_stage.fields)
            loaded_session.overrides[loaded_staged.stages[-1].key] = restored_overrides
            st.session_state.staged_sessions[loaded_module_code] = loaded_session
        return PAGE_VOLUMES

    # --- версия многоэтапного расчёта ---
    if "pending_version_load" in st.session_state:
        version_id = st.session_state.pop("pending_version_load")
        # Всё чтение — до первого изменения session_state: ошибка на любом шаге
        # не оставляет сессию наполовину восстановленной.
        try:
            loaded = read_version(store, version_id)
        except OPEN_ERRORS as exc:
            report_open_error(f"версию #{version_id}", exc)
        apply_version(loaded)
        return stage_page(loaded.staged, loaded.session.current)

    # --- все разделы расчёта с одним именем (кнопка «Открыть все разделы расчёта») ---
    if "pending_calculation_load" in st.session_state:
        name = st.session_state.pop("pending_calculation_load")
        try:
            version_ids = latest_versions_of_calculation(store, name)
            if not version_ids:
                raise KeyError(f"версий с именем «{name}» нет")
            loads = [read_version(store, version_id) for version_id in version_ids.values()]
        except OPEN_ERRORS as exc:
            report_open_error(f"расчёт «{name}»", exc)
        # Разделы, которых в расчёте нет, очищаются: иначе в сводный бюджет вошёл
        # бы раздел другого объекта, открытый в этой вкладке раньше.
        for code in MODULES:
            if code not in version_ids:
                clear_module_input(code)
        for loaded in loads:
            apply_version(loaded)
        st.session_state["module_choice"] = loads[0].module_code
        if all(loaded.calc_date is not None for loaded in loads):
            when = f"дата расчёта {st.session_state['calc_date']:%d.%m.%Y}."
        else:
            when = "дата расчёта сохранена не у всех версий — проверьте её в боковой панели."
        st.session_state["flash_calc_date"] = (
            f"Открыт расчёт «{name}», разделов: {len(loads)}; {when}"
        )
        return PAGE_BUDGET
    # --- черновик: версии, от которых продолжали, + ввод этапа ---
    if "pending_draft_load" in st.session_state:
        draft_id = st.session_state.pop("pending_draft_load")
        try:
            loaded_draft = store.load_draft(draft_id)
            loaded_staged = as_staged(MODULES[loaded_draft.module_code]["class"]())
            loaded_session = StagedSession(loaded_staged)
            if loaded_draft.base_version_id is not None:
                _code, _name, loaded_results = store.load_version_chain(loaded_draft.base_version_id)
                loaded_session.restore(loaded_results)
                loaded_session.version_id = loaded_draft.base_version_id
            loaded_session.apply_draft(loaded_draft.stage_key, loaded_draft.inputs, loaded_draft.edits)
        except OPEN_ERRORS as exc:
            report_open_error(f"черновик (id={draft_id})", exc)
        st.session_state.staged_sessions[loaded_draft.module_code] = loaded_session
        st.session_state["module_choice"] = loaded_draft.module_code
        st.session_state["save_smeta_name"] = loaded_draft.name
        apply_object_data(loaded_draft.object_data)  # нет у черновика — поля очищаются
        restore_session_form(loaded_draft.module_code, loaded_staged, loaded_session)
        set_open_warning(legacy_sampling_price_warning(
            loaded_draft.module_code,
            [loaded_session.result(s.key) for s in loaded_staged.stages
             if loaded_session.result(s.key) is not None],
            draft_edits=loaded_draft.edits,
        ))
        return stage_page(loaded_staged, loaded_session.current)
    return None


@dataclass
class LoadedVersion:
    """Прочитанная из базы версия многоэтапного расчёта — всё, что нужно, чтобы
    восстановить раздел во вкладке (`apply_version`)."""

    version_id: int
    module_code: str
    name: str
    results: list
    staged: StagedModule
    session: StagedSession
    object_data: dict | None
    cost: tuple | None
    calc_date: str | None


def read_version(store, version_id):
    """Чтение версии без изменения session_state (ошибка — OPEN_ERRORS):
    сначала читаем всё, потом меняем состояние вкладки."""
    module_code, name, results = store.load_version_chain(version_id)
    if module_code not in MODULES:
        raise KeyError(f"модуль {module_code!r} в этой версии программы не известен")
    staged = as_staged(MODULES[module_code]["class"]())
    session = StagedSession(staged)
    session.restore(results)
    return LoadedVersion(
        version_id, module_code, name, results, staged, session,
        store.get_version_object_data(version_id),
        store.load_cost_calc_for_chain(version_id),
        store.get_version_calc_date(version_id),
    )


def apply_version(loaded):
    """Прочитанная версия → раздел в этой вкладке: подтверждённые этапы, имя
    расчёта, данные объекта, дата расчёта, ввод себестоимости, формы этапов."""
    loaded.session.version_id = loaded.version_id  # следующие подтверждения — от этой версии
    st.session_state.staged_sessions[loaded.module_code] = loaded.session
    st.session_state["module_choice"] = loaded.module_code
    st.session_state["save_smeta_name"] = loaded.name
    # Версия без данных объекта / без расчёта себестоимости — поля
    # очищаются: иначе смета пересчиталась бы с данными другого объекта.
    apply_object_data(loaded.object_data)
    from app import ui_minimum

    if loaded.module_code == "geology":
        apply_cost_inputs(loaded.cost[0] if loaded.cost is not None else None)
        # общие затраты — из версии; нет в версии — сброс в 0 (иначе минимум считался бы
        # с общими затратами, возможно, другого объекта)
        ui_minimum.apply_shared(loaded.cost[0].get("_shared") if loaded.cost is not None else None)
    else:
        ui_minimum.apply_section_cost_inputs(
            loaded.module_code, loaded.cost[0] if loaded.cost is not None else None)
    if loaded.calc_date is not None:
        st.session_state["calc_date"] = date.fromisoformat(loaded.calc_date)
        st.session_state["flash_calc_date"] = (
            f"Открыта версия #{loaded.version_id}: дата расчёта {st.session_state['calc_date']:%d.%m.%Y}."
        )
    else:
        st.session_state["flash_calc_date"] = (
            f"Открыта версия #{loaded.version_id}: дата расчёта с версией не сохранена — "
            "оставлена текущая."
        )
    restore_session_form(loaded.module_code, loaded.staged, loaded.session)
    set_open_warning(legacy_sampling_price_warning(loaded.module_code, loaded.results))


def latest_versions_of_calculation(store, name):
    """{раздел: id последней версии} расчёта с именем `name` (порядок — как
    разделы в меню)."""
    latest = {}
    for version_id, _parent, version_name, code, _stage, _at in store.list_versions():
        if version_name == name and code in MODULES and code not in latest:
            latest[code] = version_id  # list_versions — новые сверху
    return {code: latest[code] for code in MODULES if code in latest}


def calculation_names(store):
    """Имена расчётов (цепочек версий), новые сверху, без повторов."""
    names = []
    for _id, _parent, name, code, _stage, _at in store.list_versions():
        if code in MODULES and name not in names:
            names.append(name)
    return names


def clear_module_input(module_code):
    """Убрать из вкладки ввод и этапы раздела (перед открытием расчёта, где
    этого раздела нет)."""
    staged = as_staged(MODULES[module_code]["class"]())
    st.session_state.staged_sessions[module_code] = StagedSession(staged)
    for stage in staged.stages:
        form_key, key_code = stage_form_keys(module_code, staged, stage)
        st.session_state.form_data_by_module.pop(form_key, None)
        reset_form_widgets(key_code, stage.fields)


# Находка ревью №4 (25.10.2026): у экологии коэффициент вида пробы (0,9 / 1,2)
# раньше входил в цену строки пробы, с 25.10.2026 — отдельный множитель в coefs.
# Правка цены пробы, сделанная до этого, могла уже включать коэффициент — тогда
# он применится дважды. Данные не мигрируем (неизвестно, включала ли правка
# коэффициент): только предупреждение и инструкция при открытии.
SAMPLING_COEF_MOVED = "2026-10-26"


def legacy_sampling_price_warning(module_code, results, draft_edits=()):
    """Текст предупреждения или None: есть ли в открываемой смете экологии
    правка цены строки пробы, сделанная до переноса коэффициента вида пробы."""
    if module_code != "ecology":
        return None
    old_edits = [
        edit for result in results if result.confirmed_at < SAMPLING_COEF_MOVED
        for edit in result.edits
    ] + list(draft_edits)
    lines = sorted({
        e.line_id for e in old_edits if e.line_id.startswith("ecology.sampling.") and e.field == "price"
    })
    if not lines:
        return None
    return (
        "В открытой смете экологии есть правка цены пробы, сделанная до 25.10.2026 ("
        + ", ".join(lines) + "). С 25.10.2026 коэффициент вида пробы (0,9 / 1,2) учитывается "
        "отдельно от цены: если в исправленной цене он уже был учтён, сейчас он применится "
        "дважды. Что сделать: на странице «3. Максимальный бюджет» откройте «Правка строк», "
        "проверьте цену этих проб (должна быть цена справочника без коэффициента), при "
        "необходимости исправьте и подтвердите смету заново."
    )


def set_open_warning(text):
    """Сообщение после открытия версии/черновика; несколько — через пустую строку
    (одинаковое — один раз: при открытии всех разделов расчёта оно приходит от
    каждого раздела)."""
    if text:
        previous = st.session_state.get("open_warning")
        if previous and text in previous.split("\n\n"):
            return
        st.session_state["open_warning"] = f"{previous}\n\n{text}" if previous else text


# --- боковая панель ---------------------------------------------------------


def module_status_text(code):
    """Статус шагов модуля для боковой панели: «ведомость — …, смета — …»."""
    session = st.session_state.staged_sessions.get(code)
    # Сессия могла появиться от простого открытия страницы (например,
    # «Минимальный бюджет» создаёт пустую сессию геологии) — без ввода и
    # снимков это тот же «не начат».
    if session is None or not any(
        session.inputs.get(s.key) or session.result(s.key) is not None for s in session.staged.stages
    ):
        return "не начат"
    parts = []
    for stage in session.staged.stages:
        status = session.status(stage.key)
        if session.result(stage.key) is None and status == STATUS_DRAFT:
            text = "не подтверждена" if session.inputs.get(stage.key) else "не начата"
        else:
            text = STATUS_LABELS[status]
        parts.append(f"{stage.title.lower()} — {text}")
    return "; ".join(parts)


def render_sidebar():
    """Боковая панель — на каждой странице: где ты в процессе. Текущий
    объект, дата расчёта и режим индексов (нужны итогам на любой странице),
    раздел изысканий (страницы 2–4), имя расчёта (для версий и черновиков),
    статус шагов по модулям. Всё это виджеты, которые рисуются на каждом
    прогоне, поэтому их значения не теряются при переходе между страницами."""
    object_data = run_context().object_data
    with st.sidebar:
        st.markdown("**Объект**")
        if object_data.code or object_data.name:
            st.caption(" — ".join(v for v in (object_data.code, object_data.name) if v))
        else:
            st.caption("не заполнен (п. 1 «Объект и ТЗ»)")
        warnings = object_data.warnings()
        if warnings:
            st.caption(f"Предупреждений по данным объекта: {len(warnings)} — см. п. 1.")

        st.date_input("Дата расчёта", key="calc_date", format="DD.MM.YYYY", persist_state="session",
                      help=ui_help.field_help("sidebar", "calc_date"))
        st.checkbox(
            "Строгий режим индексов",
            key="index_strict", persist_state="session",
            help="Включено — если индекс на дату расчёта не внесён в справочник, итога с "
            "индексацией и НДС нет. Выключено — берётся последний известный индекс с "
            "предупреждением.",
        )
        st.radio(
            "Раздел изысканий",
            options=list(MODULES.keys()),
            format_func=lambda code: MODULES[code]["label"],
            key="module_choice", persist_state="session",
            help=ui_help.field_help("sidebar", "module_choice"),
        )
        st.text_input(
            "Имя сметы для сохранения",
            key="save_smeta_name", persist_state="session",
            help="Имя расчёта: под ним сохраняются версии (каждое подтверждение этапа) и "
            "черновики.",
        )
        st.markdown("**Статус шагов**")
        for code, info in MODULES.items():
            st.caption(f"{info['label']}: {module_status_text(code)}")


def current_module():
    """(код, модуль, этапы) раздела, выбранного в боковой панели."""
    module_code = st.session_state.get("module_choice") or next(iter(MODULES))
    module = MODULES[module_code]["class"]()
    return module_code, module, as_staged(module)


def show_module_flash(module_code):
    flash = st.session_state.pop(f"flash_{module_code}", None)
    if flash is not None:
        kind, text = flash
        (st.success if kind == "ok" else st.error)(text)


# --- страницы этапов ------------------------------------------------------------


def render_stage_page(page_key):
    """Страница этапа выбранного модуля: «Ведомость объёмов» (этап 1) или
    «Максимальный бюджет» (этап 2). Редактируется только этап, на котором
    сейчас стоит расчёт (`session.current`) и только на своей странице;
    на «чужой» странице — подтверждённый снимок для чтения или подсказка,
    куда идти. Логика этапов (core/stages.py) не меняется."""
    module_code, module, staged = current_module()
    st.caption(f"Раздел изысканий: **{MODULES[module_code]['label']}** (выбор — в боковой панели).")
    show_module_flash(module_code)

    if staged.is_single_stage:
        if page_key == PAGE_VOLUMES:
            render_single_stage_module(staged, module_code, module)
        else:
            st.info(
                "У этого модуля один этап: ввод и смета — на странице «2. Ведомость объёмов»."
            )
            st.page_link(page_path(PAGE_VOLUMES), label="Перейти к ведомости")
        return

    session = st.session_state.staged_sessions.setdefault(module_code, StagedSession(staged))
    if stage_page(staged, session.current) == page_key:
        render_staged_module(staged, session, module_code, module)
        return

    current_index = staged.index(session.current)
    page_stages = [s for s in staged.stages if stage_page(staged, s.key) == page_key]
    render_stage_bar(session)
    for stage in page_stages:
        snapshot = session.result(stage.key)
        next_page = stage_page(staged, session.current)
        if staged.index(stage.key) < current_index:
            # этап подтверждён, расчёт ушёл дальше — снимок только для чтения
            st.success(
                f"Этап {stage.number} «{stage.title}» подтверждён "
                f"{format_timestamp(snapshot.confirmed_at)} — показан только для чтения."
            )
            with block(f"Утверждено: {stage.title}"):
                render_lines_table(snapshot.lines)
            with action_panel():
                st.page_link(
                    page_path(next_page), label=f"Дальше: {PAGES[next_page][1]}",
                    icon=":material/arrow_forward:",
                )
                secondary_actions_label()
                if st.button(
                    f"Вернуться к этапу «{stage.title}»", key=f"reopen_{module_code}_{stage.key}",
                    help="Этап откроется на доработку; следующие этапы будут помечены "
                    "«устарел» (не удаляются) и потребуют переподтверждения.",
                ):
                    session.reopen(stage.key)
                    go_to_stage(staged, session)
        else:
            # более ранний этап ещё не подтверждён (или открыт на доработку)
            current_title = staged.stage(session.current).title
            st.info(
                f"Этап {stage.number} «{stage.title}» ещё недоступен. Сначала подтвердите этап "
                f"«{current_title}» — этот этап считается от утверждённой ведомости."
            )
            with action_panel():
                st.page_link(
                    page_path(next_page), label=f"Перейти: {PAGES[next_page][1]}",
                    icon=":material/arrow_forward:",
                )
            if snapshot is not None:
                with st.expander("Прежняя (устаревшая) сохранённая копия этого шага"):
                    render_lines_table(snapshot.lines)


def render_single_stage_module(staged, module_code, module):
    """Одноэтапный модуль (адаптер core/stages.py::as_staged) — прежний
    сценарий "форма → Рассчитать → смета", без изменений для оператора.
    У текущих пяти модулей не используется (все двухэтапные)."""
    refs = run_context().refs
    single_stage = staged.stages[0]
    overrides: OverrideLog = st.session_state.overrides_by_module.setdefault(module_code, OverrideLog())
    estimate_key = f"estimate__{module_code}"
    stored_data = st.session_state.form_data_by_module.setdefault(module_code, {})

    attempted_key = f"validation_attempted_{module_code}"
    attempted = st.session_state.get(attempted_key, False)

    # Предварительный список незаполненных полей — по значениям, УЖЕ
    # сохранённым для этого модуля (не по key= виджетов — см. комментарий
    # у form_data_by_module), нужен, чтобы подсветить поля красным
    # СРАЗУ, до того как они будут созданы ниже.
    pre_missing = missing_required_fields(single_stage.fields, stored_data) if attempted else []

    data = render_input_fields(
        single_stage.fields, module, module_code, module_code, refs, stored_data, pre_missing
    )

    st.session_state.form_data_by_module[module_code] = data
    missing = missing_required_fields(single_stage.fields, data)
    below_min = below_min_fields(single_stage.fields, data)
    if attempted and missing:
        st.error("Заполните обязательные поля: " + ", ".join(missing))
    elif any(f.required for f in single_stage.fields):
        st.caption(REQUIRED_NOTE)
    if below_min:
        st.error("Значение меньше допустимого (отрицательное): " + ", ".join(below_min))
    missing = missing + below_min

    if st.button("Рассчитать"):
        st.session_state[attempted_key] = True
        if missing:
            st.rerun()  # перерисовать поля выше уже с красной подсветкой
        else:
            st.session_state[estimate_key] = single_stage.compute(data, {}, refs, {})

    if estimate_key in st.session_state:
        base_estimate = st.session_state[estimate_key]
        estimate = overrides.effective_estimate(base_estimate)

        for warning in single_stage.check(estimate):
            st.warning(warning)

        data_table(estimate_rows(estimate))
        totals = render_estimate_totals(estimate, module)
        render_estimate_download(estimate, module, totals)
        render_overrides_block(estimate, overrides, form_prefix=f"edit_form_{module_code}")


# --- страница «Минимальный бюджет» и сводка для «Сводного бюджета» -------------


def render_minimum_summary(geology_cost):
    """Три числа геологии (максимум по СБЦ / минимум-себестоимость /
    коэффициент снижения) на странице «Сводный бюджет» — из того же ввода,
    что на странице «Минимальный бюджет» (без виджетов ввода).
    `geology_cost` — `ui_minimum.geology_numbers()`, посчитанный один раз на прогон."""
    if geology_cost is None:
        st.info(
            "Геология: себестоимость считается после подтверждения ведомости объёмов "
            "(п. 2) и ввода на странице «4. Минимальный бюджет»."
        )
    else:
        result, red, status = geology_cost
        cols = st.columns(3)
        cols[0].metric("Геология — максимум: смета по СБЦ (без НДС)", money_text(red.maximum))
        cols[1].metric("Минимум: себестоимость", money_text(red.minimum))
        cols[2].metric("Коэффициент снижения", coefficient_text(red))
        st.caption(REDUCTION_NOTE)
        if not red.calibrated:
            render_rates_status(status)
        if red.warnings:
            st.warning("\n\n".join(red.warnings))
        fot_text = fot_share_text(result) if result.complete else None
        if fot_text:
            st.caption(fot_text + " Структура по статьям — на странице «4. Минимальный бюджет».")
        if result.missing:
            st.info(
                "Себестоимость неполная — не заполнено на странице «4. Минимальный бюджет»: "
                + ", ".join(result.missing)
            )
    from app import ui_minimum  # все разделы и объект (трек 3, 03.11.2026)

    ui_minimum.render_object_summary(geology_cost)


# --- страница «Справочники» -----------------------------------------------------


def render_param_rules_table():
    """Справочник правил выбора параметров (`data/pravila_parametrov.json`) — только
    чтение: что программа подставляет, что стоит в эталоне, статус, источник, вопрос."""
    st.caption(
        "Параметры, которые раньше брали «из готовой сметы»: коэффициенты листов, районные, "
        "транспорт, неблагоприятный период. «Допущение — ждёт ответа» — значение программы до "
        "ответа специалиста. Ответ вносит администратор программы правкой справочника правил."
    )
    error = param_rules.load_error()
    if error:
        st.error(f"Справочник правил параметров не читается — программа работает на прежних "
                 f"значениях разделов. {error}")
        return
    labels = {**{code: info["label"] for code, info in MODULES.items()}, "object": "Данные объекта"}
    data_table(
        [
            {
                "Раздел": labels.get(r.module, r.module),
                "Параметр": r.title,
                "По умолчанию": param_rules.default_label(r)
                if r.status != param_rules.STATUS_INPUT else "—",
                "В эталоне": param_rules.format_value(r.etalon_value)
                if r.etalon_value is not None else "",
                "Где в эталоне": r.etalon_where,
                "Статус": r.status_label,
                "От чего зависит": r.depends_on,
                "Источник": r.source,
            }
            for r in param_rules.load_rules().values()
        ],
        hide_index=True,
    )


def render_references_page():
    """Просмотр справочников с источниками — только чтение: расценки и
    индексы (data/references.sqlite, собирается из эталонов), ставка НДС,
    нормы себестоимости (data/norms/*.json) и архив себестоимости."""
    base_refs = run_context().base_refs
    tab_prices, tab_indices, tab_norms, tab_archive, tab_rules = st.tabs(
        ["Расценки", "Индексы и НДС", "Нормы себестоимости", "Архив себестоимости",
         "Правила параметров"]
    )
    with tab_rules:
        render_param_rules_table()
    with tab_prices:
        prices = base_refs.all_prices()
        sources = sorted({row["source"] for row in prices})
        chosen = st.multiselect("Сборник", sources, default=sources, key="refs_sources")
        rows = [
            {"Сборник": r["source"], "Таблица": r["table"], "Пункт": r["item"],
             "Категория": r["category"] or "", "Цена (база)": r["price"],
             "Наименование": r["name"], "Основание": r["basis"]}
            for r in prices if r["source"] in chosen
        ]
        st.caption(
            f"Позиций: {len(rows)} из {len(prices)}. Источник — эталонные сметы "
            "Автодороги и Карьера."
        )
        data_table(rows, hide_index=True)
    with tab_indices:
        data_table(
            [
                {"Вид работ": MODULES.get(r["work_type"], {}).get("label", r["work_type"]),
                 "Индекс": r["value"], "Начало": r["period_start"], "Конец": r["period_end"],
                 "Основание": r["basis"]}
                for r in base_refs.all_indices()
            ],
            hide_index=True,
        )
        try:
            vat_now = base_refs.get_vat_entry(calc_date_iso()).label()
        except KeyError as exc:
            st.warning(f"Ставка НДС на дату расчёта не найдена: {exc}")
        else:
            st.write(f"На дату расчёта: {vat_now}.")
        data_table(
            [
                {"Ставка НДС": f"{round(r.rate * 100):d} %", "Начало": r.period_start,
                 "Конец": "бессрочно" if r.period_end.startswith("9999") else r.period_end,
                 "Основание": r.basis}
                for r in base_refs.all_vat_rates()
            ],
            hide_index=True,
        )
    with tab_norms:
        try:
            norms = load_cost_norms("geology_cost")  # с общими ставками (core/cost.py)
        except (OSError, ValueError) as exc:
            st.warning(f"Нормы не прочитаны: {exc}")
        else:
            st.caption(
                "Геология: допущения программы; ставки компании текущего года — только на этом компьютере. "
                "Значение компании перекрывает "
                "допущение."
            )
            data_table(
                [
                    {"Норма": n.key, "Значение": n.value, "Ед.": n.unit,
                     "Откуда": "локально (компании)" if n.origin == ORIGIN_LOCAL else "допущение",
                     "Допущение": "да" if n.assumption else "", "Источник": n.source,
                     "Примечание": n.note}
                    for n in norms.values()
                ],
                hide_index=True,
            )
            try:
                local_inputs = cost_input_defaults("geology_cost")
            except (OSError, ValueError) as exc:
                st.warning(f"Локальный слой не прочитан: {exc}")
                local_inputs = {}
            if local_inputs:
                st.markdown("**Оклады по умолчанию (локальный слой)**")
                data_table(
                    [{"Поле": name, "Значение, руб./мес": money_text(v.value), "Источник": v.source}
                     for name, v in local_inputs.items()],
                    hide_index=True,
                )
        from app import ui_minimum  # общие нормы и нормы остальных разделов

        ui_minimum.render_norms_tables()
    with tab_archive:
        try:
            archive, archive_source = load_archive()
        except FileNotFoundError:
            st.caption("Архив себестоимости к этой сборке не подключён.")
        except (ValueError, KeyError) as exc:
            st.warning(f"Архив себестоимости не прочитан: {exc}")
        else:
            if is_demo_archive(archive_source):
                st.info(DEMO_ARCHIVE_NOTE)
            data_table(
                [
                    {"Объект": o.code, "Годы": o.years, "Метраж, п.м.": o.meters,
                     "Факт 1 п.м., руб.": o.fact_cost_per_m,
                     "Норматив 1 п.м., руб.": o.norm_cost_per_m, "Примечание": o.note or ""}
                    for o in archive
                ],
                hide_index=True,
            )
            st.caption(f"{ARCHIVE_CAPTION} Источник: {archive_source}.")


# --- страница «Версии» ----------------------------------------------------------


def _meaningful(value):
    """Введено ли что-то кроме пустых/нулевых значений по умолчанию."""
    if isinstance(value, dict):
        return any(_meaningful(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_meaningful(v) for v in value)
    return value not in (None, 0, 0.0, "", False)


def module_has_input(module_code):
    """У раздела в этой вкладке уже есть ввод (не нули по умолчанию) или
    подтверждённые этапы."""
    session = st.session_state.staged_sessions.get(module_code)
    if session is not None and any(
        _meaningful(session.inputs.get(s.key)) or session.result(s.key) is not None
        for s in session.staged.stages
    ):
        return True
    return _meaningful(st.session_state.form_data_by_module.get(module_code))


def confirm_replace(target, module_code):
    """Опасное действие «Открыть…» (docs/ui_guide.md §3): заменяет ввод раздела
    в этой вкладке. Если ввода нет — сразу True. Если есть — первое нажатие
    только предупреждает, второе нажатие по тому же объекту — True."""
    if not module_has_input(module_code) or st.session_state.get("confirm_replace") == target:
        st.session_state.pop("confirm_replace", None)
        st.session_state.pop("confirm_replace_text", None)
        return True
    st.session_state["confirm_replace"] = target
    st.session_state["confirm_replace_text"] = (
        f"В разделе «{MODULES[module_code]['label']}» в этой вкладке уже есть ввод. Открытие "
        "заменит его (несохранённое пропадёт; сохранённые версии и черновики не трогаются). "
        "Нажмите кнопку «Открыть» ещё раз, чтобы продолжить."
    )
    return False


def confirm_replace_all(target):
    """То же, что `confirm_replace`, для открытия всех разделов расчёта: оно
    заменяет ввод КАЖДОГО раздела во вкладке (разделы, которых в расчёте нет,
    очищаются)."""
    with_input = [code for code in MODULES if module_has_input(code)]
    if not with_input or st.session_state.get("confirm_replace") == target:
        st.session_state.pop("confirm_replace", None)
        st.session_state.pop("confirm_replace_text", None)
        return True
    st.session_state["confirm_replace"] = target
    labels = ", ".join(f"«{MODULES[code]['label']}»" for code in with_input)
    st.session_state["confirm_replace_text"] = (
        f"В этой вкладке уже есть ввод разделов: {labels}. Открытие расчёта заменит ввод всех "
        "разделов (несохранённое пропадёт; сохранённые версии и черновики не трогаются). "
        "Нажмите кнопку «Открыть все разделы расчёта» ещё раз, чтобы продолжить."
    )
    return False


def render_versions_page():
    """Сохранённые сметы, версии многоэтапных расчётов и черновики:
    открытие (с переходом на страницу этапа). Сохранение версий —
    автоматически при подтверждении этапа, черновика — кнопкой на экране
    этапа; здесь — только сохранение одноэтапной сметы (у текущих модулей
    не используется)."""
    store = run_context().store
    module_code, module, staged = current_module()
    smeta_name = st.session_state.get("save_smeta_name") or ""

    col_save, col_open = st.columns(2)
    with col_save:
        st.markdown("**Сохранение**")
        if not staged.is_single_stage:
            st.caption(
                "Каждое подтверждение этапа сохраняется автоматически — новая версия в "
                "цепочке под именем из боковой панели («Имя сметы для сохранения»). "
                "Незавершённый ввод без подтверждения — кнопка «Сохранить черновик» на "
                "экране этапа (один черновик на раздел и имя, перезаписывается)."
            )
        elif st.button("Сохранить смету", disabled=not smeta_name):
            # SmetaStoreBusyError — ожидаемая, не редкая ситуация теперь,
            # когда программа умеет открываться несколько раз одновременно
            # (см. app_launcher.py::_find_free_port,
            # записи разработки): два экземпляра
            # могут попытаться сохранить смету в один и тот же момент.
            # `core.persistence.SmetaStore` сама уже повторяет попытку
            # несколько раз — если и это не помогло, показываем понятную
            # ошибку оператору, а не роняем всё приложение.
            data = st.session_state.form_data_by_module.get(module_code, {})
            overrides = st.session_state.overrides_by_module.setdefault(module_code, OverrideLog())
            try:
                smeta_id = store.save_smeta(smeta_name, module.code, data)
                store.save_edits(smeta_id, overrides.history())
                st.success(f"Смета «{smeta_name}» сохранена (№ {smeta_id}).")
            except SmetaStoreBusyError as exc:
                st.error(f"Не удалось сохранить смету: {exc}")

    with col_open:
        st.markdown("**Открыть**")
        saved = store.list_smety()
        if saved:
            labels_by_id = {
                smeta_id: f"{name} — {MODULES.get(mod_code, {}).get('label', mod_code)} ({created_at})"
                for smeta_id, name, mod_code, created_at in saved
            }
            selected_id = st.selectbox(
                "Открыть сохранённую смету",
                options=list(labels_by_id.keys()),
                format_func=lambda smeta_id: labels_by_id[smeta_id],
            )
            if st.button("Открыть") and confirm_replace(
                ("smeta", selected_id), next(m for i, _n, m, _c in saved if i == selected_id)
            ):
                try:
                    loaded_module_code, loaded_input_data, loaded_edits = store.load_smeta(selected_id)
                    if loaded_module_code not in MODULES:
                        raise KeyError(
                            f"модуль {loaded_module_code!r} в этой версии программы не известен"
                        )
                except OPEN_ERRORS as exc:
                    report_open_error(f"смету (id={selected_id})", exc)
                st.session_state["pending_load"] = {
                    "module_code": loaded_module_code,
                    "input_data": loaded_input_data,
                    "edits": loaded_edits,
                }
                st.rerun()

        versions = [row for row in store.list_versions() if row[3] in MODULES]
        if not saved and not versions and not store.list_drafts():
            st.write("Сохранённых смет пока нет.")
        if versions:
            # Все разделы одного расчёта сразу (одно имя цепочек версий): последняя
            # версия каждого раздела — в эту вкладку, затем сводный бюджет.
            names = calculation_names(store)

            def calculation_label(name):
                codes = latest_versions_of_calculation(store, name)
                return f"{name} — разделов: {len(codes)}"

            selected_name = st.selectbox(
                "Открыть все разделы расчёта (последняя версия каждого раздела)",
                options=names,
                format_func=calculation_label,
                key="open_calculation_choice",
            )
            if st.button("Открыть все разделы расчёта") and confirm_replace_all(
                ("calculation", selected_name)
            ):
                st.session_state["pending_calculation_load"] = selected_name
                st.rerun()

            def version_label(row):
                version_id, parent_id, name, mod_code, stage_key, confirmed_at = row
                stage_title = as_staged(MODULES[mod_code]["class"]()).stage(stage_key).title
                parent = f", от #{parent_id}" if parent_id is not None else ""
                return (
                    f"#{version_id} {name} — {MODULES[mod_code]['label']}, «{stage_title}» "
                    f"({format_timestamp(confirmed_at)}{parent})"
                )

            labels_by_version = {row[0]: version_label(row) for row in versions}
            selected_version = st.selectbox(
                "Открыть версию многоэтапного расчёта",
                options=list(labels_by_version.keys()),
                format_func=lambda version_id: labels_by_version[version_id],
                key="open_version_choice",
            )
            if st.button("Открыть версию") and confirm_replace(
                ("version", selected_version),
                next(row[3] for row in versions if row[0] == selected_version),
            ):
                st.session_state["pending_version_load"] = selected_version
                st.rerun()

        drafts = [row for row in store.list_drafts() if row[2] in MODULES]
        if drafts:
            def draft_label(row):
                _draft_id, name, mod_code, stage_key, saved_at = row
                stage_title = as_staged(MODULES[mod_code]["class"]()).stage(stage_key).title
                return (
                    f"{name} — {MODULES[mod_code]['label']}, черновик этапа «{stage_title}» "
                    f"({format_timestamp(saved_at)})"
                )

            labels_by_draft = {row[0]: draft_label(row) for row in drafts}
            selected_draft = st.selectbox(
                "Открыть черновик",
                options=list(labels_by_draft.keys()),
                format_func=lambda draft_id: labels_by_draft[draft_id],
                key="open_draft_choice",
            )
            if st.button("Открыть черновик") and confirm_replace(
                ("draft", selected_draft),
                next(row[2] for row in drafts if row[0] == selected_draft),
            ):
                st.session_state["pending_draft_load"] = selected_draft
                st.rerun()
    replace_warning = st.session_state.get("confirm_replace_text")
    if replace_warning:
        st.warning(replace_warning)
    st.caption(
        "Сравнение версий между собой — планируется."
    )
