"""Хвост прогона «дальше» №3: выпадающие списки — на белой подложке, как
остальные поля ввода (components/Field.md).

В Streamlit 1.64 st.selectbox — react-aria ComboBox (`.react-aria-ComboBox
[role="group"]`), а не baseweb select: прежнее правило темы
(`[data-baseweb="select"] > div`) его не задевало, и поле оставалось серым
(secondaryBackgroundColor = surface-sunken) со светлой рамкой. Проверено живьём
(Playwright, скриншоты до/после — work/briefs/scripts_dd_dovodka/): фон поля
списка до — surface-sunken, после — surface-raised, как у числовых полей."""

import re

from app import theme

GROUP = '.stApp [data-testid="stSelectbox"] .react-aria-ComboBox [role="group"]'


def _rules(css: str, selector: str) -> list[str]:
    """Тела правил, в списке селекторов которых есть `selector`."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # комментарии — не селекторы
    return [body for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
            if selector in [s.strip() for s in selectors.split(",")]]


def test_selectbox_field_is_raised_like_other_inputs():
    css = theme.CSS_PATH.read_text(encoding="utf-8")
    bodies = _rules(css, GROUP)
    assert any("background: var(--surface-raised)" in b and "border-color: var(--border-strong)" in b
               for b in bodies), bodies
    # у числового поля — та же подложка и рамка
    number = _rules(css, '.stApp [data-testid="stNumberInputContainer"]')
    assert any("background: var(--surface-raised)" in b and "border-color: var(--border-strong)" in b
               for b in number)
    # фокус виден: рамка цвета фокуса
    assert any("border-color: var(--focus)" in b for b in _rules(css, GROUP + ":focus-within"))
