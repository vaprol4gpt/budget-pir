"""2. Ведомость объёмов — первый этап модулей (что и сколько делать)."""

import streamlit as st

from app import ui, ui_import

ui.page_header(
    "2. Ведомость объёмов",
    "Введите объёмы работ по разделу, выбранному в боковой панели, и подтвердите ведомость — "
    "она зафиксируется версией, и откроется смета (п. 3).",
)
# Черновик из ТЗ — над вводом; заполняется после отрисовки этапа, чтобы показать
# значения ввода этого прогона (место резервируется заранее).
tz_provenance = st.container()
excel_slot = ui_import.render_import_block()  # ведомость из Excel: до формы — загрузка заменяет ввод
ui.render_stage_page(ui.PAGE_VOLUMES)
ui_import.render_export(excel_slot)
with tz_provenance:
    ui.render_tz_provenance()
st.caption(
    "Общая ведомость по всем разделам объекта — планируется; сейчас — "
    "по одному разделу."
)
