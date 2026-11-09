"""Страница «1. Объект и ТЗ»: режим «ключевые объёмы» генератора ведомости
(core/tz.py, 03.11.2026). Вынесено из `app/ui.py` (общий файл правят несколько
треков); `app/ui.py` вызывает отсюда три функции минимальными вставками.

Инженер выбирает, как предлагать ведомость: только по параметрам ТЗ и решениям
исполнителя или по ключевым объёмам разделов. Во втором случае по каждому
выбранному в блоке ТЗ разделу — 3–5 полей главных объёмов (их задаёт модуль,
`key_volume_fields()`); кнопка «Предложить ведомость» заполняет ввод этапа
ведомости тем же механизмом (повтор не трогает ручной ввод), остальное модуль
достраивает по жёстким связям и оценкам архива, с происхождением каждого
значения на странице 2.

Виджеты: режим — `tz_propose_mode`, ключевые объёмы — `obj_tz_kv_<раздел>_<поле>`
(persist_state="session", как у параметров ТЗ); значения хранятся в
`TzParams.key_volumes` (тот же JSON данных объекта).
"""

from __future__ import annotations

import streamlit as st

from core.tz import (
    PROPOSE_KEY,
    PROPOSE_MODE_LABELS,
    PROPOSE_TZ,
    SECTION_LABELS,
    TzParams,
    key_volume_fields,
    key_volume_options,
)

MODE_KEY = "tz_propose_mode"


def kv_key(code: str, name: str) -> str:
    return f"obj_tz_kv_{code}_{name}"


def propose_mode() -> str:
    """Выбранный режим предложения (по умолчанию — по ТЗ, как было)."""
    mode = st.session_state.get(MODE_KEY)
    return mode if mode in PROPOSE_MODE_LABELS else PROPOSE_TZ


def key_volumes_from_state(modules: dict) -> dict:
    """Ключевые объёмы из виджетов: {раздел: {поле: число}} (пустые — пропущены)."""
    result = {}
    for code, module in modules.items():
        values = {
            f.name: st.session_state.get(kv_key(code, f.name)) for f in key_volume_fields(module)
        }
        values = {k: v for k, v in values.items() if v not in (None, 0, 0.0, "")}
        if values:
            result[code] = values
    return result


def apply_key_volume_widgets(tz: TzParams, modules: dict) -> None:
    """Ключевые объёмы сохранённой версии/черновика — в виджеты (до отрисовки)."""
    for code, module in modules.items():
        saved = tz.key_volumes.get(code, {})
        for f in key_volume_fields(module):
            value = saved.get(f.name)
            if f.type == "text":  # выпадающий список: код варианта или None
                value = value if isinstance(value, str) else None
            elif value is not None:
                # целое — до ближайшего, как key_value в core/tz.py (ревью трека 1, №7)
                value = int(round(value)) if f.integer else float(value)
            st.session_state[kv_key(code, f.name)] = value


def render_key_volumes(modules: dict, sections) -> None:
    """Переключатель режима и поля ключевых объёмов выбранных разделов."""
    st.radio(
        "Как предлагать ведомость", list(PROPOSE_MODE_LABELS), format_func=PROPOSE_MODE_LABELS.get,
        key=MODE_KEY, persist_state="session",
        help="«По ключевым объёмам»: вы задаёте по разделу 3–5 главных объёмов (число скважин, "
        "площадь съёмки, водотоки…), программа достраивает остальные строки по жёстким связям "
        "(например, анализ = пробе) и по оценкам архива — их нужно проверить.",
    )
    if propose_mode() != PROPOSE_KEY:
        return
    st.markdown("**Ключевые объёмы разделов** — главные объёмы, которые задаёт инженер")
    st.caption(
        "Пусто — не задано: поле останется как при предложении по ТЗ. Остальные строки ведомости "
        "программа достроит: «жёсткая связь» проверена на двух эталонных объектах и имеет "
        "физический смысл; «оценка по архиву» — проверьте. Откуда каждое значение — на "
        "странице «2. Ведомость объёмов»."
    )
    shown = [code for code in sections if code in modules and key_volume_fields(modules[code])]
    if not shown:
        st.info("Отметьте разделы изысканий выше — для них появятся поля ключевых объёмов.")
        return
    for code in shown:
        label = SECTION_LABELS[code].split(" (")[1].rstrip(")").capitalize()
        st.markdown(f"*{label}*")
        fields = key_volume_fields(modules[code])
        cols = st.columns(min(len(fields), 3))
        for number, f in enumerate(fields):
            with cols[number % len(cols)]:
                if f.type == "text":
                    options = dict(key_volume_options(modules[code], f.name))
                    st.selectbox(f.label, [None, *options],
                                 format_func=lambda v, o=options: "не задано" if v is None else o[v],
                                 key=kv_key(code, f.name), persist_state="session")
                elif f.integer:
                    st.number_input(f.label, value=None, min_value=0, step=1,
                                    key=kv_key(code, f.name), persist_state="session")
                else:
                    st.number_input(f.label, value=None, min_value=0.0, format="%0.2f",
                                    key=kv_key(code, f.name), persist_state="session")
