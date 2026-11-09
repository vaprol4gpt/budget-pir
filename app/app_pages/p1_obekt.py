"""1. Объект и ТЗ — общие данные объекта (шаг 0) и параметры из ТЗ."""

import streamlit as st

from app import ui

ui.page_header(
    "1. Объект и ТЗ",
    "Поступило техническое задание (ТЗ): внесите данные объекта и параметры ТЗ — они общие для "
    "всех разделов изысканий. Дата расчёта и режим индексов — в боковой панели.",
)

ui.render_object_block()
ui.render_tz_block()
ui.render_tz_skipped()

with ui.action_panel():
    if ui.primary_action(
        "Перейти к ведомости объёмов", "goto_volumes",
        "Данные объекта и параметры ТЗ сохраняются сами; дальше — ввод объёмов работ по разделу.",
    ):
        st.switch_page(ui.page_path(ui.PAGE_VOLUMES))
    ui.secondary_actions_label()
    # Второстепенное (docs/ui_guide.md §3): предложение необязательно — ТЗ объёмов не
    # задаёт, черновик всё равно проверяет и дополняет оператор на странице 2.
    ui.render_tz_propose_button()
