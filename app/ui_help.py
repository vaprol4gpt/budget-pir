"""Справка в программе (03.11.2026): раскрывающаяся
«Как работать с этим шагом» под шапкой каждой страницы и подсказки «?» у полей.

Текст — в одном файле данных `app/help_texts.json`: его же берёт генератор
руководства оператора `tools/make_rukovodstvo.py` (строки справки страницы —
начало её раздела в руководстве), чтобы тексты программы и руководства не
расходились (`tests/test_spravka.py`). Страница находится по заголовку, с которым
она вызывает `ui.page_header` (`pages.<ключ>.header`).

Файл не прочитался (повреждён, нет в сборке) — справки и подсказок просто нет,
программа работает как без них: справка не должна мешать расчёту."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import streamlit as st

HELP_PATH = Path(__file__).with_name("help_texts.json")
# Подписи раскрывающейся справки: у шагов процесса и у служебных страниц.
STEP_LABEL = "Как работать с этим шагом"
SERVICE_LABEL = "Как работать с этой страницей"
# Где подробнее — понятно и пользователю exe (у него нет папки проекта).
GUIDE_NOTE = ("Подробно — «Руководство оператора» (файл Руководство_оператора.docx) в разделе "
              "руководства публичного репозитория программы; в exe — эта справка на каждой "
              "странице.")


@lru_cache(maxsize=1)
def load_help(path: str = str(HELP_PATH)) -> dict:
    """Тексты справки ({} — файл не прочитан). Кэш на процесс: правка файла —
    перезапуск программы (как у справочника правил параметров)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _section(data, key) -> dict:
    """Вложенный словарь `data[key]`; не словарь (битый тип в файле) — {}."""
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def page_key_for_header(header: str) -> str | None:
    """Ключ страницы (как в `ui.PAGES`) по заголовку шапки или None."""
    for key, page in _section(load_help(), "pages").items():
        if isinstance(page, dict) and page.get("header") == header:
            return key
    return None


def page_steps(page_key: str) -> list[str]:
    """Строки «как работать» страницы (пусто — справки нет; не строки — пропускаются)."""
    steps = _section(_section(load_help(), "pages"), page_key).get("steps")
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, str) and s.strip()]


def field_help(group: str, name: str, default: str | None = None) -> str | None:
    """Подсказка поля `name` группы `group` («object», «tz», «sidebar»,
    «geology_cost») или `default`. Не строка (битый тип) — `default`."""
    text = _section(_section(load_help(), "fields"), group).get(name)
    return text if isinstance(text, str) and text else default


def render_page_help(header: str, is_step: bool) -> None:
    """Раскрывающаяся справка под шапкой страницы (свёрнута; не кнопка и не
    рамка «Действие» — docs/ui_guide.md). Нет текста — ничего не рисуется."""
    key = page_key_for_header(header)
    steps = page_steps(key) if key else []
    if not steps:
        return
    with st.expander(STEP_LABEL if is_step else SERVICE_LABEL):
        st.markdown("\n".join(f"{n}. {text}" for n, text in enumerate(steps, 1)))
        st.caption(GUIDE_NOTE)
