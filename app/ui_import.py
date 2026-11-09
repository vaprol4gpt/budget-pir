"""Страница «2. Ведомость объёмов»: блок «Ведомость из Excel» (03.11.2026, трек
«импорт ведомости»). Загрузка заполненного шаблона → отчёт об ошибках по строкам и
предпросмотр → «Поставить в ведомость» (второстепенная кнопка; главная кнопка
страницы — «Подтвердить ведомость») → тот же путь, что при ручном вводе. Выгрузка
текущего ввода и пустого шаблона раздела. Чтение и проверка — без Streamlit,
`core/vedomost_import.py`; книга — `core/vedomost_template.py`."""

from __future__ import annotations

import hashlib

import streamlit as st

from app import ui
from core.stages import StagedSession
from core.vedomost_import import (
    build_catalog,
    export_rows,
    has_input,
    iter_issue_rows,
    read_vedomost,
)
from core.vedomost_template import SECTION_TITLES, template_file_name, workbook_bytes

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PENDING_KEY = "pending_vedomost_import"
CONFIRM_KEY = "vedomost_import_confirm"


def _uploader_key(module_code):
    """Ключ загрузчика: после «Поставить в ведомость» счётчик растёт — поле очищается."""
    counter = st.session_state.get(f"vedomost_upload_n_{module_code}", 0)
    return f"vedomost_upload_{module_code}_{counter}"


def _section_title(module_code):
    return SECTION_TITLES.get(module_code, ui.MODULES[module_code]["label"])


def _first_stage_input(module_code, staged, session):
    """Текущий ввод этапа ведомости: форма (пока этап редактируется) или
    подтверждённый снимок."""
    first = staged.stages[0]
    if session.current == first.key:
        form_key, _ = ui.stage_form_keys(module_code, staged, first)
        return st.session_state.form_data_by_module.get(form_key) or session.inputs.get(first.key) or {}
    snapshot = session.result(first.key)
    return dict(snapshot.inputs) if snapshot is not None else {}


def apply_pending_import(module_code, staged, session):
    """Нажата «Поставить в ведомость» (прошлый прогон): ввод этапа ведомости
    заменяется загруженным ДО отрисовки формы — виджеты формы сбрасываются, как при
    открытии версии (`ui.reset_form_widgets`)."""
    pending = st.session_state.get(PENDING_KEY)
    if pending is None or pending["module"] != module_code:
        return
    st.session_state.pop(PENDING_KEY)
    first = staged.stages[0]
    if session.current != first.key:
        return
    form_key, key_code = ui.stage_form_keys(module_code, staged, first)
    st.session_state.form_data_by_module[form_key] = dict(pending["inputs"])
    session.inputs[first.key] = dict(pending["inputs"])
    ui.reset_form_widgets(key_code, first.fields)
    # Черновик из ТЗ к этому вводу больше не относится.
    st.session_state.get("tz_proposals", {}).pop(module_code, None)
    counter_key = f"vedomost_upload_n_{module_code}"
    st.session_state[counter_key] = st.session_state.get(counter_key, 0) + 1
    st.session_state[f"flash_{module_code}"] = (
        "ok", f"Объёмы из файла «{pending['name']}» перенесены в форму ведомости (строк файла: "
        f"{pending['rows']}). Ведомость ещё не подтверждена — проверьте её ниже, при необходимости "
        "поправьте и подтвердите.",
    )


def render_import_block():
    """Блок «Ведомость из Excel» раздела, выбранного в боковой панели. Вызывается
    до отрисовки этапа (`ui.render_stage_page`)."""
    module_code, module, staged = ui.current_module()
    session = st.session_state.staged_sessions.setdefault(module_code, StagedSession(staged))
    apply_pending_import(module_code, staged, session)
    if staged.is_single_stage:
        return None
    refs = ui.run_context().refs
    catalog = build_catalog(module, refs, module_code)
    first = staged.stages[0]
    editable = session.current == first.key
    current = _first_stage_input(module_code, staged, session)
    uploader_key = _uploader_key(module_code)
    uploaded = st.session_state.get(uploader_key)

    with ui.block("Ведомость из Excel (необязательно)"):
        st.caption(
            "Отдел может прислать ведомость объёмов файлом Excel по шаблону раздела — программа "
            "проверит его и перенесёт объёмы в форму ведомости. Дальше всё как при ручном вводе."
        )
        with st.expander("Загрузить из Excel или скачать в шаблон", expanded=uploaded is not None):
            # Кнопки скачивания заполняются после формы (`render_export`): текущий ввод
            # известен только после отрисовки полей этого прогона.
            export_slot = st.container()
            st.file_uploader("Файл ведомости (.xlsx)", type=["xlsx"], key=uploader_key)
            sum_duplicates = st.checkbox(
                "Сложить одинаковые строки (ведомость по сооружениям)",
                key=f"vedomost_sum_{module_code}",
                help="Если вид работ повторяется (по сооружениям или этапам), объёмы складываются. "
                "Без отметки повтор — ошибка.",
            )
            if uploaded is not None:
                _render_upload(module_code, catalog, module, refs, uploaded, sum_duplicates,
                               editable, current, first)
    return export_slot


def render_export(slot):
    """«Скачать пустой шаблон» и «Скачать в шаблон Excel» (текущий ввод) — в место,
    зарезервированное `render_import_block`; вызывается после отрисовки этапа."""
    if slot is None:
        return
    module_code, module, staged = ui.current_module()
    session = st.session_state.staged_sessions[module_code]
    catalog = build_catalog(module, ui.run_context().refs, module_code)
    title = _section_title(module_code)
    current = _first_stage_input(module_code, staged, session)
    rows, skipped = export_rows(catalog, current)
    with slot:
        row = st.container(horizontal=True)
        with row:
            st.download_button(
                "Скачать пустой шаблон", workbook_bytes(catalog, title),
                file_name=template_file_name(module_code), mime=XLSX_MIME,
                key=f"vedomost_template_{module_code}",
            )
        with row:
            st.download_button(
                "Скачать в шаблон Excel", workbook_bytes(catalog, title, rows),
                file_name=f"vedomost_{module_code}_tekushchaya.xlsx", mime=XLSX_MIME,
                key=f"vedomost_export_{module_code}", disabled=not rows,
                help="Текущий ввод ведомости в тот же шаблон (без нулевых объёмов). Правки "
                "строк ведомости в файл не попадают — только ввод." if rows else
                "Ввод ведомости пуст — выгружать нечего.",
            )
        for text in skipped:
            st.warning("Не выгружено: " + text)


def _render_upload(module_code, catalog, module, refs, uploaded, sum_duplicates, editable,
                   current, first):
    data = uploaded.getvalue()
    labels = {code: info["label"] for code, info in ui.MODULES.items()}
    result = read_vedomost(data, module, refs, module_code, labels, sum_duplicates)
    if result.errors:
        st.error(
            f"В файле ошибок: {len(result.errors)} — в ведомость он не поставлен. Исправьте файл "
            "и загрузите снова."
        )
        ui.data_table(iter_issue_rows(result.errors), hide_index=True)
    if result.warnings:
        st.warning(
            f"Замечаний: {len(result.warnings)}. Файл можно поставить в ведомость, но сначала "
            "прочитайте их — возможно, в файле чего-то не хватает."
        )
        ui.data_table(iter_issue_rows(result.warnings), hide_index=True)
    if result.preview:
        st.markdown("**Что попадёт в форму ведомости**")
        ui.data_table([
            {"Строка Excel": p.row, "Вид работ": p.work, "Объём": p.qty, "Единица": p.unit,
             "Параметры строки": p.params, "Примечание": p.note}
            for p in result.preview
        ], hide_index=True)

    reason = None
    if result.errors:
        reason = "в файле есть ошибки"
    elif not editable:
        reason = (f"ведомость подтверждена — чтобы загрузить файл, вернитесь к этапу "
                  f"«{first.title}»")
    digest = hashlib.sha1(data + bytes([sum_duplicates])).hexdigest()
    target = f"{module_code}:{digest}"
    replace = reason is None and has_input(catalog, current)
    if replace and st.session_state.get(CONFIRM_KEY) == target:
        st.warning(
            "В форме ведомости уже есть ввод — он будет заменён значениями файла (правки строк "
            "ведомости и сохранённые версии не трогаются). Нажмите «Поставить в ведомость» ещё "
            "раз, чтобы продолжить."
        )
    if st.button("Поставить в ведомость", key=f"vedomost_apply_{module_code}",
                 disabled=reason is not None):
        if replace and st.session_state.get(CONFIRM_KEY) != target:
            st.session_state[CONFIRM_KEY] = target
            st.rerun()
        st.session_state.pop(CONFIRM_KEY, None)
        st.session_state[PENDING_KEY] = {
            "module": module_code, "inputs": result.inputs, "name": uploaded.name,
            "rows": len(result.preview),
        }
        st.rerun()
    if reason:
        st.caption(f"«Поставить в ведомость» сейчас недоступно: {reason}.")
