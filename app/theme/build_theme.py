"""Сборка темы интерфейса из дизайн-кода компании (docs/design/firm-design-code/).

Первоисточник оформления — `tokens.json` и `firm.css` дизайн-кода; руками их здесь не
переписываем. Скрипт генерирует:

- `app/theme/firm_theme.css` — CSS-переменные обеих тем («Белая сталь» — `:root`,
  «Чёрная сталь» — `:root:has(.vs-theme-dark)`: метку темы ставит `app/theme/__init__.py`
  по `st.context.theme.type`), классы `vs-*` из `firm.css` и правила, которые подгоняют
  под них разметку Streamlit (меню, панели, кнопки, сообщения, поля);
- `.streamlit/config.toml` — `[theme.light]` / `[theme.dark]` (+ `.sidebar`) из тех же
  токенов: primaryColor = accent, backgroundColor = surface, secondaryBackgroundColor =
  surface-sunken, textColor = ink;
- `app/theme/theme_flags.json` — те же настройки флагами командной строки для exe
  (`app_launcher.py`: exe запускается не из папки проекта и `.streamlit/config.toml` не видит),
  плюс `[client] toolbarMode = "viewer"` — без кнопки Deploy;
- `app/theme/logos/` — копии логотипов, которые показывает программа.

Запуск: `.venv/Scripts/python.exe -m app.theme.build_theme` (из корня проекта).
`--check` — ничего не пишет, только сверяет: код выхода 1, если файлы устарели
(то же проверяет `tests/test_theme.py`)."""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIR = ROOT / "docs" / "design" / "firm-design-code"
TOKENS_PATH = DESIGN_DIR / "tokens.json"
FIRM_CSS_PATH = DESIGN_DIR / "firm.css"
THEME_DIR = Path(__file__).resolve().parent
CSS_PATH = THEME_DIR / "firm_theme.css"
FLAGS_PATH = THEME_DIR / "theme_flags.json"
LOGOS_DIR = THEME_DIR / "logos"
CONFIG_PATH = ROOT / ".streamlit" / "config.toml"

# Логотипы программы: меню шагов — надпись «компания» (изумрудная на светлом фоне, белая на
# тёмном), свёрнутая боковая панель — эмблема (components/StepNav.md, logos/README.md).
LOGO_FILES = (
    "firm-wordmark-emerald.png",
    "firm-wordmark-white.png",
    "firm-emblem-emerald.png",
    "firm-emblem-white.png",
)

GOOGLE_FONTS = {
    "display": ("Manrope", "Manrope:wght@500;600;700;800"),
    "sans": ("Golos Text", "Golos+Text:wght@400;500;600;700"),
    "mono": ("JetBrains Mono", "JetBrains+Mono:wght@400;500"),
}
FONTS_URL = "https://fonts.googleapis.com/css2?family={}&display=swap"

# Настройка Streamlit → токен дизайн-кода (для каждой темы своё значение токена).
THEME_COLORS = {
    "primaryColor": "accent",
    "backgroundColor": "surface",
    "secondaryBackgroundColor": "surface-sunken",
    "textColor": "ink",
    "borderColor": "border",
    "linkColor": "accent-ink",
    "codeTextColor": "ink",
    "codeBackgroundColor": "surface-sunken",
    "dataframeBorderColor": "border",
    "dataframeHeaderBackgroundColor": "surface-sunken",
    "greenColor": "success",
    "greenBackgroundColor": "success-soft",
    "greenTextColor": "success",
    "orangeColor": "warning",
    "orangeBackgroundColor": "warning-soft",
    "orangeTextColor": "warning",
    "yellowColor": "warning",
    "redColor": "danger",
    "redBackgroundColor": "danger-soft",
    "redTextColor": "danger",
    "blueColor": "accent-ink",
    "blueBackgroundColor": "surface-raised",
    "blueTextColor": "ink",
    "grayColor": "ink-muted",
    "grayTextColor": "ink-muted",
}
# Боковая панель — меню шагов на surface-sunken; поля в ней — белые (surface-raised).
SIDEBAR_COLORS = {
    "backgroundColor": "surface-sunken",
    "secondaryBackgroundColor": "surface-raised",
    "borderColor": "border",
}


def load_tokens(path=TOKENS_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def color_values(tokens, theme):
    """{имя токена: цвет} для темы `light` / `dark` (общие ступени — одинаковые)."""
    values = {}
    for token in tokens["color"]["tokens"]:
        value = token["value"]
        values[token["name"]] = value[theme] if isinstance(value, dict) else value
    return values


def type_styles(tokens):
    return {s["name"]: s for group in tokens["type"]["groups"] for s in group["styles"]}


def css_variables(tokens, theme):
    lines = [f"  --{name}: {value};" for name, value in color_values(tokens, theme).items()]
    if theme == "light":
        for name, family in tokens["type"]["families"].items():
            lines.append(f"  --font-{name}: {family};")
        for group in ("spacing", "radius"):
            lines += [f"  --{t['name']}: {t['value']};" for t in tokens[group]["tokens"]]
    for token in tokens["shadow"]["tokens"]:
        lines.append(f"  --{token['name']}: {token['value'][theme]};")
    return "\n".join(lines)


def design_rules(firm_css):
    """Правила `firm.css` без `@import` шрифтов (он — первой строкой файла темы) и без
    правила `body` (фон, шрифт и кегль страницы задаёт тема Streamlit)."""
    kept = [
        line for line in firm_css.splitlines()
        if not line.startswith("@import") and not line.startswith("body ")
    ]
    # Белый текст на градиенте в firm.css записан цветом — подставляем его токен (steel-0),
    # чтобы в правилах не было ни одного цвета мимо переменных.
    return "\n".join(kept).strip().replace("#FFFFFF", "var(--steel-0)")


# Подгонка разметки Streamlit 1.64 под компоненты дизайн-кода (components/*.md).
# Только var(--…): ни одного цвета или размера мимо токенов.
STREAMLIT_RULES = """
/* --- Подгонка под Streamlit (генерируется app/theme/build_theme.py) --- */

/* Шрифты: текст — Golos Text, заголовки — Manrope, шифры — JetBrains Mono. */
.stApp, .stApp input, .stApp textarea, .stApp button, [data-testid="stSidebar"] { font-family: var(--font-sans); }
.stApp h1, .stApp h2, .stApp h3, .stApp h4 { font-family: var(--font-display); }
.stApp code, .vs-code { font-family: var(--font-mono); }

/* Меню шагов (components/StepNav.md) */
[data-testid="stSidebar"] { border-right: 1px solid var(--border); }
[data-testid="stNavSectionHeader"] { font: 600 12px/16px var(--font-sans); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-muted); padding-top: var(--space-4); }
[data-testid="stSidebarNavLink"] { border-radius: var(--radius-sm); color: var(--ink); font-weight: 500; gap: var(--space-2); }
[data-testid="stSidebarNavLink"]:hover { background: var(--surface-hover); }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: var(--accent-soft); font-weight: 600; }
[data-testid="stSidebarNavLink"][aria-current="page"] span { color: var(--ink); }
/* Номер шага в квадрате и значок статуса справа: текст (content) — в app/theme/__init__.py
   по адресу страницы, у служебных пунктов номера нет — ::before у них пустой. */
[data-testid="stSidebarNavLink"]::before { font: 700 13px/1 var(--font-display); color: var(--ink-muted); min-width: 24px; height: 24px; display: inline-grid; place-items: center; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); font-variant-numeric: tabular-nums; box-sizing: border-box; flex: none; }
[data-testid="stSidebarNavLink"][aria-current="page"]::before { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
[data-testid="stSidebarNavLink"]::after { margin-left: auto; font: 700 13px/1 var(--font-sans); }
[data-testid="stSidebarNavSeparator"] { border-color: var(--border); }
[data-testid="stLogo"] { height: 28px; max-width: 100%; }

/* Рамки находим по невидимым меткам (app/theme/__init__.py::mark); метки панелей места не занимают. */
[data-testid="stElementContainer"]:has(> [data-testid="stHtml"] > .vs-mark-panel), [data-testid="stElementContainer"]:has(> [data-testid="stHtml"] > .vs-mark-action) { display: none; }

/* Шапка шага (components/PageHeader.md): глубокий градиент, белый текст. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) { background: linear-gradient(160deg, var(--gradient-via) 0%, var(--emerald-800) 50%, var(--gradient-to) 100%); color: var(--steel-0); border-radius: var(--radius-md); padding: var(--space-6); gap: var(--space-2); }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) h1, [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) h2, [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) h3 { color: var(--steel-0); font: 700 28px/34px var(--font-display); letter-spacing: -.01em; padding: 0; margin: 0; }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) [data-testid="stHeadingWithActionElements"] a { display: none; }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) [data-testid="stCaptionContainer"], [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) [data-testid="stCaptionContainer"] * { color: var(--steel-0) !important; opacity: 1; font-size: 15px; line-height: 22px; max-width: 75ch; }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) [data-testid="stHeading"] { margin-bottom: var(--space-2); }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) [data-testid="stCaptionContainer"] { margin-top: var(--space-1); }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) .vs-header__step { color: var(--steel-0); }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) .vs-header__meta { margin-top: var(--space-2); color: var(--steel-0); }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) .vs-header__meta > div > span { display: block; opacity: .85; }
/* Бейдж на градиенте — всегда белая плашка с тёмным изумрудным текстом (в обеих темах:
   цвета бейджей тёмной темы рассчитаны на тёмный фон и на белом не читаются). */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-header) .vs-badge { background: var(--steel-0); color: var(--emerald-900); opacity: 1; }

/* Панели (components/Panel.md): белая карточка, рамка border, радиус radius-md. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-panel) { background: var(--surface-raised); border-color: var(--border) !important; border-radius: var(--radius-md) !important; }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-panel) > [data-testid="stElementContainer"]:has(> [data-testid="stHtml"] > .vs-mark-panel) + [data-testid="stElementContainer"] [data-testid="stMarkdownContainer"] p, [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-panel) > [data-testid="stElementContainer"]:has(> [data-testid="stHtml"] > .vs-mark-panel) + [data-testid="stElementContainer"] [data-testid="stMarkdownContainer"] strong { font: 700 20px/28px var(--font-display); color: var(--ink); }

/* Панель «Действие» (components/ActionPanel.md): верхняя изумрудная линия. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-action) { background: var(--surface-raised); border-color: var(--border) !important; border-top: 3px solid var(--accent) !important; border-radius: var(--radius-md) !important; }
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .vs-mark-action) > [data-testid="stElementContainer"]:has(> [data-testid="stHtml"] > .vs-mark-action) + [data-testid="stElementContainer"] [data-testid="stMarkdownContainer"] p { font: 600 12px/16px var(--font-sans); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-muted); }
.vs-action__more { border-top: 1px solid var(--border); padding-top: var(--space-3); margin-top: var(--space-2); }
.vs-action__reason { color: var(--warning); font-size: 13px; line-height: 18px; margin: 0; }

/* Кнопки (components/Button.md) */
.stApp button[kind="primary"], .stApp button[data-testid="stBaseButton-primary"] { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; min-height: 48px; padding: 0 var(--space-5); }
.stApp button[kind="primary"] p { color: var(--on-accent); font-size: 15px; }
.stApp button[kind="primary"]:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); color: var(--on-accent); }
.stApp button[kind="primary"]:disabled { background: var(--surface-sunken); border-color: var(--border); color: var(--ink-muted); }
.stApp button[kind="primary"]:disabled p { color: var(--ink-muted); }
.stApp button[kind="secondary"], .stApp [data-testid="stBaseButton-secondary"] { background: var(--surface-raised); border-color: var(--border-strong); color: var(--ink); }
.stApp button[kind="secondary"]:hover:not(:disabled) { background: var(--surface-hover); border-color: var(--border-strong); color: var(--ink); }
.stApp button[kind="secondary"]:disabled, .stApp [data-testid="stBaseButton-secondary"]:disabled { background: var(--surface-sunken); border-color: var(--border); color: var(--ink-muted); }
.stApp button[kind="secondary"]:disabled p { color: var(--ink-muted); }
/* Флажки и переключатели: контур border-strong, иначе пустой флажок в тёмной теме почти не виден. */
.stApp [data-testid="stCheckbox"] label > span:first-child, .stApp [data-testid="stRadio"] label > div:first-child { border: 1px solid var(--border-strong); }
.stApp :focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }

/* Сообщения (components/Callout.md): текст на ink, цвет несут рамка и значок. */
.stApp [data-testid="stAlertContainer"] { border: 1px solid var(--border-strong); border-radius: var(--radius-md); color: var(--ink); }
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) { background: var(--surface-raised); border-color: var(--border-strong); }
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { background: var(--warning-soft); border-color: var(--warning); }
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) { background: var(--danger-soft); border-color: var(--danger); }
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { background: var(--success-soft); border-color: var(--success); }
.stApp [data-testid^="stAlertContent"] { color: var(--ink); display: grid; grid-template-columns: 20px 1fr; gap: var(--space-3); }
.stApp [data-testid^="stAlertContent"] p, .stApp [data-testid^="stAlertContent"] li { color: var(--ink); }
.stApp [data-testid^="stAlertContent"]::before { font-weight: 700; text-align: center; }
.stApp [data-testid="stAlertContentInfo"]::before { content: "i"; color: var(--accent-ink); }
.stApp [data-testid="stAlertContentWarning"]::before { content: "!"; color: var(--warning); }
.stApp [data-testid="stAlertContentError"]::before { content: "×"; color: var(--danger); }
.stApp [data-testid="stAlertContentSuccess"]::before { content: "✓"; color: var(--success); }

/* Поля ввода (components/Field.md): белое поле, рамка border-strong, кольцо фокуса. */
.stApp [data-testid="stTextInputRootElement"], .stApp [data-testid="stTextAreaRootElement"], .stApp [data-testid="stNumberInputContainer"], .stApp [data-testid="stDateInputField"], .stApp [data-baseweb="select"] > div { background: var(--surface-raised); border-color: var(--border-strong); border-radius: var(--radius-sm); }
.stApp [data-testid="stTextInputRootElement"] *, .stApp [data-testid="stTextAreaRootElement"] *, .stApp [data-testid="stNumberInputContainer"] > div, .stApp [data-testid="stNumberInputField"], .stApp [data-baseweb="select"] > div > div { background: var(--surface-raised); color: var(--ink); font-variant-numeric: tabular-nums; }
.stApp [data-testid="stNumberInputStepDown"], .stApp [data-testid="stNumberInputStepUp"] { background: var(--surface-raised); color: var(--ink-muted); }
/* Выпадающий список Streamlit 1.64 — react-aria ComboBox, а не baseweb select: правило выше
   его не задевает, и поле оставалось серым (secondaryBackgroundColor) со светлой рамкой —
   Доработка 03.11. Белое поле и рамка border-strong, как у остальных полей. */
.stApp [data-testid="stSelectbox"] .react-aria-ComboBox [role="group"], .stApp [data-testid="stMultiSelect"] .react-aria-ComboBox [role="group"] { background: var(--surface-raised); border-color: var(--border-strong); border-radius: var(--radius-sm); color: var(--ink); }
.stApp [data-testid="stSelectbox"] .react-aria-ComboBox [role="group"]:focus-within, .stApp [data-testid="stMultiSelect"] .react-aria-ComboBox [role="group"]:focus-within { border-color: var(--focus); }
.stApp [data-testid="stNumberInputStepDown"]:hover, .stApp [data-testid="stNumberInputStepUp"]:hover { background: var(--accent-soft); color: var(--accent-ink); }
.stApp [data-testid="stWidgetLabel"] p { font: 600 13px/18px var(--font-sans); color: var(--ink); }
.vs-field__hint { font-size: 12px; line-height: 16px; color: var(--ink-muted); margin: 0; }

/* Выбранные значения в списках — бейджи brand (components/StatusBadge.md), не яркий изумруд. */
.stApp [data-baseweb="tag"] { background: var(--accent-soft); color: var(--accent-ink); border-radius: var(--radius-xs); }
.stApp [data-baseweb="tag"] span, .stApp [data-baseweb="tag"] svg { color: var(--accent-ink); fill: var(--accent-ink); }

/* Таблицы (components/DataTable.md) — рамка и радиус; заголовки — прописными (app/ui.py). */
.stApp [data-testid="stDataFrame"], .stApp [data-testid="stTable"] { border-radius: var(--radius-md); }
.stApp [data-testid="stTable"] th { background: var(--surface-sunken); font: 600 12px/16px var(--font-sans); letter-spacing: .04em; text-transform: uppercase; color: var(--ink-muted); }

/* Итоговые цифры (components/Figures.md): st.metric — как карточка vs-figure. */
.stApp [data-testid="stMetric"] { background: var(--surface-raised); border: 1px solid var(--border); border-radius: var(--radius-md); padding: var(--space-4) var(--space-5); }
.stApp [data-testid="stMetricLabel"], .stApp [data-testid="stMetricLabel"] * { white-space: normal; overflow: visible; text-overflow: clip; }
.stApp [data-testid="stMetricLabel"] p { font: 600 12px/16px var(--font-sans); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-muted); }
.stApp [data-testid="stMetricValue"] { font: 700 32px/36px var(--font-display); letter-spacing: -.01em; font-variant-numeric: tabular-nums; color: var(--ink); }
.vs-figures { margin: var(--space-2) 0; }

/* Раскрывающиеся блоки внутри панелей — без лишней тени. */
.stApp [data-testid="stExpander"] details { border-color: var(--border); border-radius: var(--radius-md); background: var(--surface-raised); }

/* Статусы шагов в боковой панели */
.vs-status-list { display: flex; flex-direction: column; gap: var(--space-1); margin: 0 0 var(--space-2); }
"""


def build_css(tokens, firm_css):
    families = "&family=".join(spec for _family, spec in GOOGLE_FONTS.values())
    return "\n".join([
        f"@import url('{FONTS_URL.format(families)}');",
        "",
        "/* ВНИМАНИЕ: файл генерируется — app/theme/build_theme.py из",
        "   docs/design/firm-design-code/tokens.json и firm.css. Руками не править. */",
        "",
        "/* «Белая сталь» — светлая тема (по умолчанию) */",
        ":root {",
        css_variables(tokens, "light"),
        "}",
        "",
        "/* «Чёрная сталь» — тёмная тема */",
        ":root:has(.vs-theme-dark) {",
        css_variables(tokens, "dark"),
        "}",
        "",
        "/* --- Классы дизайн-кода (docs/design/firm-design-code/firm.css) --- */",
        design_rules(firm_css),
        STREAMLIT_RULES.rstrip(),
        "",
    ])


def font_option(key):
    family, spec = GOOGLE_FONTS[key]
    fallback = {"display": "'Golos Text', 'Segoe UI', sans-serif",
                "sans": "'Segoe UI', sans-serif",
                "mono": "Consolas, monospace"}[key]
    return f"'{family}', {fallback}:{FONTS_URL.format(spec)}"


def theme_options(tokens, theme):
    """Настройки одной темы Streamlit: {имя: значение} и {имя: значение} боковой панели."""
    colors = color_values(tokens, theme)
    styles = type_styles(tokens)
    radius = {t["name"]: t["value"] for t in tokens["radius"]["tokens"]}
    options = {key: colors[token] for key, token in THEME_COLORS.items()}
    # baseFontSize (кегль body 15 px) в секциях [theme.light]/[theme.dark] Streamlit 1.64 не
    # принимает, а верхний [theme] запирает тему — остаётся кегль Streamlit по умолчанию.
    options.update({
        "font": font_option("sans"),
        "headingFont": font_option("display"),
        "codeFont": font_option("mono"),
        "codeFontSize": styles["code"]["fontSize"],
        "headingFontSizes": [styles[n]["fontSize"] for n in ("h1", "h2", "h3")]
        + [styles["body"]["fontSize"], styles["small"]["fontSize"], styles["label"]["fontSize"]],
        "headingFontWeights": [styles[n]["fontWeight"] for n in ("h1", "h2", "h3")] + [600, 600, 600],
        "baseRadius": radius["radius-sm"],
        "buttonRadius": radius["radius-sm"],
        "showWidgetBorder": True,
        "linkUnderline": False,
    })
    sidebar = {key: colors[token] for key, token in SIDEBAR_COLORS.items()}
    return options, sidebar


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


CONFIG_HEADER = """\
# ВНИМАНИЕ: файл генерируется — app/theme/build_theme.py из дизайн-кода компании
# (docs/design/firm-design-code/tokens.json). Руками не править: правка токенов →
# перегенерация (`.venv/Scripts/python.exe -m app.theme.build_theme`).
#
# Как это работает в Streamlit 1.64 (проверено чтением исходников 25.10.2026):
# переключатель тем System/Light/Dark в меню «⋮» (справа сверху) появляется, только когда
# заполнены ОБЕ секции — [theme.light] и [theme.dark]; верхнеуровневый [theme] не задаём
# (он запирает программу в одной теме). «System» следует теме Windows/браузера; выбор
# оператора браузер запоминает. Светлая «Белая сталь» — основная тема дизайн-кода, тёмная
# «Чёрная сталь» — по выбору.
"""


# Панель инструментов Streamlit для оператора: «viewer» — без кнопки Deploy и пунктов
# разработчика; в меню «⋮» выбор темы System / Light / Dark остаётся (проверено живьём) (решение пользователя 03.11.2026).
CLIENT_OPTIONS = {"toolbarMode": "viewer"}


def build_config(tokens):
    parts = [CONFIG_HEADER, "[client]"]
    parts += [f"{key} = {toml_value(value)}" for key, value in CLIENT_OPTIONS.items()]
    parts.append("")
    for theme, title in (("light", "Белая сталь"), ("dark", "Чёрная сталь")):
        options, sidebar = theme_options(tokens, theme)
        parts.append(f"# «{title}»")
        parts.append(f"[theme.{theme}]")
        parts += [f"{key} = {toml_value(value)}" for key, value in options.items()]
        parts.append("")
        parts.append(f"[theme.{theme}.sidebar]")
        parts += [f"{key} = {toml_value(value)}" for key, value in sidebar.items()]
        parts.append("")
    return "\n".join(parts)


def build_flags(tokens):
    """Те же настройки — флагами `streamlit run` для exe (app_launcher.py). Списки
    (размеры заголовков) Streamlit во флагах не принимает — в exe они по умолчанию."""
    flags = [f"--client.{key}={value}" for key, value in CLIENT_OPTIONS.items()]
    for theme in ("light", "dark"):
        options, sidebar = theme_options(tokens, theme)
        for key, value in options.items():
            if not isinstance(value, list):
                flags.append(f"--theme.{theme}.{key}={toml_value(value).strip(chr(34))}")
        for key, value in sidebar.items():
            flags.append(f"--theme.{theme}.sidebar.{key}={value}")
    return json.dumps(flags, ensure_ascii=False, indent=1) + "\n"


def outputs():
    """{путь: текст} всех генерируемых текстовых файлов."""
    tokens = load_tokens()
    firm_css = FIRM_CSS_PATH.read_text(encoding="utf-8")
    return {
        CSS_PATH: build_css(tokens, firm_css),
        CONFIG_PATH: build_config(tokens),
        FLAGS_PATH: build_flags(tokens),
    }


def present_logos():
    """Логотипы, которые есть в дизайн-коде. Они фирменные: в публичной копии их нет — тогда тема
    собирается без них, а программа показывает меню без логотипа (app/theme/__init__.py::logo_files)."""
    return [name for name in LOGO_FILES if (DESIGN_DIR / "logos" / name).is_file()]


def stale_files():
    """Файлы, которые разошлись с дизайн-кодом (пусто — всё согласовано)."""
    stale = [path for path, text in outputs().items()
             if not path.exists() or path.read_text(encoding="utf-8") != text]
    for name in present_logos():
        target = LOGOS_DIR / name
        if not target.exists() or target.read_bytes() != (DESIGN_DIR / "logos" / name).read_bytes():
            stale.append(target)
    return stale


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        stale = stale_files()
        for path in stale:
            print(f"устарел: {path.relative_to(ROOT)}")
        return 1 if stale else 0
    for path, text in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" — одинаковый файл на любом ПК (иначе Windows пишет \r\n).
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"записан: {path.relative_to(ROOT)}")
    logos = present_logos()
    if logos:
        LOGOS_DIR.mkdir(exist_ok=True)
    for name in logos:
        shutil.copyfile(DESIGN_DIR / "logos" / name, LOGOS_DIR / name)
    print(f"логотипы: {len(logos)} → {LOGOS_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
