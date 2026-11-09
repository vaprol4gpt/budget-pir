"""Streamlit-интерфейс расчёта бюджета ПИР — главный скрипт (точка входа).

С 29.10.2026 интерфейс разнесён по пунктам меню, повторяющим процесс компании
(ТЗ → ведомость объёмов → максимальный бюджет → минимальный бюджет →
сводный бюджет), плюс «Справочники» и «Версии». Страницы —
`app/app_pages/*.py`, общий код — `app/ui.py`. Расчёт не менялся.

Этот скрипт выполняется на КАЖДОМ прогоне перед страницей: готовит
состояние сессии, справочник, базу смет, данные объекта, открывает
сохранённую смету/версию/черновик (если её выбрали на странице «Версии»),
рисует боковую панель и запускает выбранную страницу. Запуск —
`python -m streamlit run app/streamlit_app.py` из корня проекта
(launch_app.bat), в exe — app_launcher.py."""

import streamlit as st

import core.persistence as persistence
from app import ui
from core.object_data import CalculationContext
from core.persistence import SmetaStoreBusyError, SmetaStoreFormatError

# До любой команды Streamlit: команда может прервать прогон (пришёл новый запрос),
# а отметка «какая страница начата» должна успеть записаться (ui.py, находка О-2).
ui.resync_widgets_after_page_change()

st.set_page_config(page_title="Расчёт бюджета ПИР", layout="wide")

ui.init_session_state()

try:
    # Справочник проверяется при запуске: устарел или повреждён — пересборка
    # (из исходников) или понятная ошибка, а не молчаливый итог без НДС (С2).
    base_refs, refs_message = ui.get_refs()
except (FileNotFoundError, ui.ReferencesError) as exc:
    st.error(f"Справочник расценок недоступен. {exc}")
    st.stop()
if refs_message and not st.session_state.get("_refs_message_shown"):
    st.session_state["_refs_message_shown"] = True
    st.toast(refs_message)
try:
    store = ui.get_store(str(persistence.DEFAULT_DB_PATH))
except (SmetaStoreFormatError, SmetaStoreBusyError) as exc:
    st.error(f"База сохранённых смет недоступна: {exc}")
    st.stop()

# Открытие сметы/версии/черновика — до отрисовки виджетов (меняет их значения).
goto_page = ui.process_pending_loads(base_refs, store)

# Имя черновика, придуманное на прошлом прогоне (ui.draft_name), — в поле
# боковой панели до его отрисовки.
pending_name = st.session_state.pop("pending_save_name", None)
if pending_name is not None:
    st.session_state["save_smeta_name"] = pending_name

# Шаг 0 — данные объекта; модули считают от справочника вместе с ними
# (CalculationContext: все методы справочника + object_data).
object_data = ui.object_data_from_state()
st.session_state["_run_context"] = ui.RunContext(
    refs=CalculationContext(base_refs, object_data),
    base_refs=base_refs,
    store=store,
    object_data=object_data,
)

# У шагов процесса значка нет: вместо него номер шага в квадрате (дизайн-код компании,
# components/StepNav.md; рисует тема — ui.inject_theme), у служебных — значок.
pages = {
    key: st.Page(
        path, title=ui.nav_title(key), icon=None if ui.step_number(key) else icon,
        default=(key == ui.PAGE_OBJECT),
    )
    for key, (path, _title, icon) in ui.PAGES.items()
}
# Меню — в левой боковой панели, каждый пункт отдельной строкой, видны все
# сразу (expanded=True — без «показать ещё»): сверху шаги процесса, ниже —
# служебное (docs/ui_guide.md §1). До 30.10.2026 меню было в верхней полосе,
# и часть пунктов пряталась в «… more».
navigation = st.navigation(
    {section: [pages[key] for key in keys] for section, keys in ui.MENU_SECTIONS.items()},
    position="sidebar",
    expanded=True,
)

if goto_page is not None:
    st.switch_page(pages[goto_page])

if navigation.title != ui.nav_title(ui.PAGE_VERSIONS):
    # Ушли со страницы «Версии» — ожидание второго нажатия «Открыть…» сброшено.
    st.session_state.pop("confirm_replace", None)
    st.session_state.pop("confirm_replace_text", None)

# Оформление по дизайн-коду компании (docs/design/firm-design-code/, app/theme/) — один раз
# на прогоне, до страницы: стили, логотип, номера и статусы шагов в меню.
ui.inject_theme(pages)
ui.render_sidebar()

open_error = st.session_state.pop("open_error", None)
if open_error is not None:
    st.error(open_error)
open_warning = st.session_state.pop("open_warning", None)
if open_warning is not None:
    st.warning(open_warning)
calc_date_flash = st.session_state.pop("flash_calc_date", None)
if calc_date_flash is not None:
    st.info(calc_date_flash)

navigation.run()
