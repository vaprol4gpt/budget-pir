"""Оформление интерфейса по дизайн-коду компании (docs/design/firm-design-code/).

Стили — `firm_theme.css` (генерирует `build_theme.py` из `tokens.json`; руками не правится).
Здесь — только подключение и разметка компонентов дизайн-кода (классы `vs-*`): метка
тёмной темы, логотип, номера и значки статусов в меню шагов, шапка шага, бейджи, ряд
итоговых цифр. Ни одного цвета или размера: всё — через классы и `var(--…)`."""

from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st

THEME_DIR = Path(__file__).resolve().parent
CSS_PATH = THEME_DIR / "firm_theme.css"
LOGOS_DIR = THEME_DIR / "logos"

# Значок статуса — всегда вместе с цветом (README дизайн-кода: статус не держится на цвете).
STATUS_MARKS = {"done": "✓", "warn": "!", "error": "×", "todo": "", "brand": ""}
STATUS_COLORS = {"done": "success", "warn": "warning", "error": "danger"}


@lru_cache(maxsize=1)
def theme_css():
    return CSS_PATH.read_text(encoding="utf-8")


def theme_type():
    """«light» или «dark» — тема, выбранная в браузере (меню «⋮» справа сверху: System / Light / Dark). В AppTest и
    до первого ответа браузера тема неизвестна — светлая, основная тема дизайн-кода."""
    try:
        kind = st.context.theme.type
    except (AttributeError, RuntimeError):
        kind = None
    return "dark" if kind == "dark" else "light"


def nav_css(steps):
    """Номер шага в квадрате и значок статуса у пунктов меню шагов.
    `steps` — [(адрес страницы, номер или None, статус или None)]; адрес — url_path
    страницы, у страницы по умолчанию — пустой (её ссылка — «/»)."""
    rules = []
    for url_path, number, status in steps:
        selector = f'[data-testid="stSidebarNavLink"][href$="/{url_path}"]'
        if number is not None:
            rules.append(f'{selector}::before {{ content: "{number}"; }}')
        if status in STATUS_COLORS:
            rules.append(
                f'{selector}::after {{ content: "{STATUS_MARKS[status]}"; '
                f"color: var(--{STATUS_COLORS[status]}); }}"
            )
    return "\n".join(rules)


def inject(steps=()):
    """Подключить тему на этом прогоне: стили, метку тёмной темы, логотип в меню.
    Вызывается один раз в главном скрипте (app/streamlit_app.py) до страницы."""
    dark = theme_type() == "dark"
    marker = '<span class="vs-theme-dark" hidden></span>' if dark else ""
    st.html(f"<style>{theme_css()}\n{nav_css(steps)}</style>{marker}")
    wordmark, emblem = logo_files(dark)
    if wordmark is not None:  # сборка без логотипов (публичная копия, демо) — меню без логотипа
        st.logo(str(wordmark), size="large", icon_image=str(emblem))


def logo_files(dark):
    """(надпись, знак) логотипа для темы или (None, None), если файлов нет: логотипы — фирменные,
    в публичную копию и демо-сборку не входят, программа без них работает."""
    color = "white" if dark else "emerald"
    wordmark = LOGOS_DIR / f"firm-wordmark-{color}.png"
    emblem = LOGOS_DIR / f"firm-emblem-{color}.png"
    if wordmark.is_file() and emblem.is_file():
        return wordmark, emblem
    return None, None


def mark(kind):
    """Невидимая метка рамки (header / panel / action): по ней стили находят рамку
    (`:has(.vs-mark-…)`). Не ключ контейнера: рамки с ключами Streamlit 1.64 не убирает
    при переходе на другую страницу — старое содержимое оставалось на экране."""
    return f'<span class="vs-mark vs-mark-{kind}"></span>'


def badge(kind, text):
    """Бейдж статуса (components/StatusBadge.md): kind — done / warn / error / todo / brand."""
    mark = STATUS_MARKS.get(kind, "")
    label = f"{mark} {text}" if mark else text
    return f'<span class="vs-badge vs-badge--{kind}">{escape(label)}</span>'


def header_kicker(step_number, total_steps):
    """Строка над названием шага: двойной срез + «Шаг N из 5» (у служебных — «Служебное»)."""
    text = f"Шаг {step_number} из {total_steps}" if step_number else "Служебное"
    return f'<div class="vs-header__step"><span class="vs-slash"></span>{escape(text)}</div>'


def header_meta(items, status=None):
    """Строка под пояснением шага: [(подпись, значение)] + бейдж статуса."""
    parts = [
        f"<div><span>{escape(label)}</span><b>{escape(value)}</b></div>" for label, value in items
    ]
    if status is not None:
        parts.append(f"<div><span>Статус</span><b>{badge(*status)}</b></div>")
    return f'<div class="vs-header__meta">{"".join(parts)}</div>'


def figures(items):
    """Ряд итоговых цифр (components/Figures.md): [(подпись, значение, единица, пояснение,
    ключевая ли)]. Ключевая — не больше одной."""
    cards = []
    for label, value, unit, note, key in items:
        unit_html = f'<span class="vs-figure__unit">{escape(unit)}</span>' if unit else ""
        note_html = f'<p class="vs-figure__note">{escape(note)}</p>' if note else ""
        cards.append(
            f'<div class="vs-figure{" vs-figure--key" if key else ""}">'
            f'<div class="vs-label">{escape(label)}</div>'
            f'<div class="vs-figure__value vs-num">{escape(value)}{unit_html}</div>{note_html}</div>'
        )
    return f'<div class="vs-figures">{"".join(cards)}</div>'


def separator():
    """Черта перед второстепенными действиями панели «Действие»."""
    return '<div class="vs-action__more"></div>'


def field_hint(text):
    """Строка под полем: источник нормы или ставки (components/Field.md)."""
    return f'<p class="vs-field__hint">{escape(text)}</p>'
