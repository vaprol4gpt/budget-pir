"""Страница «5. Сводный бюджет»: цена против минимального бюджета объекта.

Вынесено из `app/ui.py` (общий файл правят несколько треков). Минимум объекта —
одна сущность программы, `core.cost.ObjectMinimum` (трек «минимум»): здесь он
только собирается по разделам, вошедшим в бюджет, и сравнивается с ценой
(`core/target_loss.py`). Одно предупреждение на страницу — «Работа в убыток на
N руб.» (по целевой цене, а без неё — по цене после снижения; если коэффициент в
поле сводной даёт цену ниже целевой — по ней) или «Цена ниже минимума раздела»;
то же уходит на лист «КП» Excel.
"""

from __future__ import annotations

import streamlit as st

from core.target_loss import target_loss


def object_minimum(aggregator, geology_cost):
    """(`ObjectMinimum` по разделам бюджета или None, названия разделов, у которых
    минимум посчитан, но сметы в бюджете нет). `geology_cost` — себестоимость
    геологии, уже посчитанная на этом прогоне (`ui_minimum.geology_numbers()`); остальные
    разделы — `ui_minimum.all_sections` (один расчёт на прогон страницы)."""
    from app import ui_minimum  # ui_minimum импортирует app.ui — только здесь

    numbers = ui_minimum.all_sections(geology_cost)
    priced = {m.module_code for m in aggregator.summary()["modules"] if m.total_with_vat is not None}
    in_budget = {code: n for code, n in numbers.items() if code in priced}
    labels = ui_minimum.ui.MODULES
    skipped = tuple(labels[code]["label"] for code in numbers if code not in priced and code in labels)
    if not in_budget:
        return None, skipped
    minimum, _red = ui_minimum.object_numbers(in_budget)
    return minimum, skipped


def loss_warning(aggregator, object_min, target):
    """Текст единственного предупреждения «цена ниже минимума» или None.
    `object_min` — пара из `object_minimum`."""
    minimum, skipped = object_min if object_min else (None, ())
    loss = target_loss(aggregator, target, minimum, skipped)
    return None if loss is None else loss.text


def render_target_loss(aggregator, object_min, target) -> str | None:
    """Показывает предупреждение (если есть) и возвращает его текст."""
    text = loss_warning(aggregator, object_min, target)
    if text:
        st.warning(text)
    return text
