"""Тема интерфейса по дизайн-коду компании (docs/design/firm-design-code/): токены, сгенерированный
CSS и config.toml согласованы; в коде страниц нет «сырых» цветов мимо темы."""

import json
import re
import tomllib
from pathlib import Path

from app import theme
from app.theme import build_theme

ROOT = Path(__file__).resolve().parents[1]
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b")


def test_generated_files_match_design_code():
    """Перегенерация ничего не меняет: CSS, config.toml, флаги exe и логотипы — из tokens.json."""
    assert build_theme.stale_files() == []


def test_config_colors_are_tokens():
    tokens = build_theme.load_tokens()
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert "base" not in config.get("theme", {})  # иначе пропадает переключатель тем
    for name in ("light", "dark"):
        colors = build_theme.color_values(tokens, name)
        section = config["theme"][name]
        assert section["primaryColor"] == colors["accent"]
        assert section["backgroundColor"] == colors["surface"]
        assert section["secondaryBackgroundColor"] == colors["surface-sunken"]
        assert section["textColor"] == colors["ink"]
    # временный бирюзовый (В-28) больше нигде не задан
    assert "#3B877D" not in (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")


def test_css_defines_every_token_for_both_themes():
    css = theme.theme_css()
    tokens = build_theme.load_tokens()
    light, dark = css.split(":root:has(.vs-theme-dark)", 1)
    for token in tokens["color"]["tokens"]:
        assert f"--{token['name']}:" in light
        if isinstance(token["value"], dict):
            assert f"--{token['name']}: {token['value']['dark']};" in dark


def test_css_rules_use_only_variables():
    """Цвета в CSS — только в блоках переменных; правила ссылаются на var(--…)."""
    css = theme.theme_css()
    rules = css.split("/* --- Классы дизайн-кода", 1)[1]
    assert HEX.findall(rules) == []


def test_no_raw_hex_colors_in_page_code():
    files = [ROOT / "app" / "ui.py", ROOT / "app" / "streamlit_app.py", ROOT / "app_launcher.py",
             ROOT / "app" / "theme" / "__init__.py", *sorted((ROOT / "app" / "app_pages").glob("*.py")),
             # код экранов треков, вынесенный из ui.py (импорт ведомости, минимум и т.п.)
             *sorted((ROOT / "app").glob("ui_*.py"))]
    found = {str(f.relative_to(ROOT)): HEX.findall(f.read_text(encoding="utf-8")) for f in files}
    assert {f: h for f, h in found.items() if h} == {}


def test_exe_flags_cover_both_themes():
    flags = json.loads(build_theme.FLAGS_PATH.read_text(encoding="utf-8"))
    tokens = build_theme.load_tokens()
    for name in ("light", "dark"):
        accent = build_theme.color_values(tokens, name)["accent"]
        assert f"--theme.{name}.primaryColor={accent}" in flags
    assert all(flag.startswith(("--theme.", "--client.")) for flag in flags)
    assert "--client.toolbarMode=viewer" in flags  # без кнопки Deploy (03.11.2026)


def test_config_hides_deploy_button():
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["toolbarMode"] == "viewer"


def test_nav_css_marks_status_with_symbol_not_only_color():
    css = theme.nav_css([("", 1, "done"), ("p2_vedomost", 2, "warn"), ("p6_spravochniki", None, None)])
    assert '[href$="/"]::before { content: "1"; }' in css
    assert 'content: "✓"' in css and 'content: "!"' in css
    assert "p6_spravochniki" not in css


def test_badge_and_figures_escape_text():
    assert "&lt;b&gt;" in theme.badge("done", "<b>")
    html = theme.figures([("Итого", "1 234,56", "руб.", "с НДС", True)])
    assert "vs-figure--key" in html and "1 234,56" in html


def test_logo_files_found_in_working_tree_or_absent_without_error(tmp_path, monkeypatch):
    """Логотипы фирменные: в публичной копии и демо-сборке их нет — меню без логотипа, не ошибка."""
    wordmark, emblem = theme.logo_files(dark=False)
    if (theme.LOGOS_DIR / "firm-wordmark-emerald.png").is_file():
        assert wordmark.is_file() and emblem.is_file()
    monkeypatch.setattr(theme, "LOGOS_DIR", tmp_path)
    assert theme.logo_files(dark=False) == (None, None)
    assert theme.logo_files(dark=True) == (None, None)
