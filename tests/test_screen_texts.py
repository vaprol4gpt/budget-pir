"""Этап З карты (аудит 05.11.2026, С-4): на экранах нет путей к файлам проекта и номеров вопросов;
короткие подписи полей; предупреждения двух уровней; строка правила без внутренних ссылок."""

import ast
import json
import re
from pathlib import Path

import pytest

from core import param_rules
from core.display import LONG_LABEL, split_label, split_warnings
from tests.test_streamlit_app import (
    _click,
    _geology_to_estimate,
    _texts,
    goto,
    isolated_smeta_db,  # noqa: F401
    open_app,
)

ROOT = Path(__file__).resolve().parents[1]
INTERNAL = re.compile(r"ВОПРОСЫ\.md|\bВ-\d+|docs/|data/|reference_data|докстринг|known_risks|\.py\b")

# Строки не для оператора — сообщения для того, кто собирает программу и её файлы; по файлу и
# началу строки, а не по файлу целиком: иначе рядом проходит текст для экрана.
ALLOWED = {
    ("core/references.py", "База справочника не найдена"),
    ("core/references.py", ". Запустите data/build_references.py"),
    ("core/references.py", ") — пересоберите справочник"),
    ("modules/geology/cost.py", "не задана (data/norms/"),
    ("core/vedomost_template.py", "Список выводится из полей ввода"),
    ("core/vedomost_template.py", ". Сделан программой"),
}


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _runtime_strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if re.search("[А-Яа-яЁё]", node.value):  # только тексты для человека
                yield node.lineno, node.value


def test_no_internal_references_in_code_strings():
    files = [*ROOT.glob("app/*.py"), *ROOT.glob("app/app_pages/*.py"),
             *ROOT.glob("modules/*/*.py"), *ROOT.glob("core/*.py")]
    found = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        found += [f"{rel}:{line}: {text[:100]}" for line, text in _runtime_strings(path)
                  if INTERNAL.search(text)
                  and not any(rel == file and text.lstrip().startswith(start) for file, start in ALLOWED)]
    assert found == []


def test_no_internal_references_in_help_and_rule_texts():
    help_texts = json.loads((ROOT / "app" / "help_texts.json").read_text(encoding="utf-8"))
    help_texts.pop("_about", None)
    assert not INTERNAL.search(json.dumps(help_texts, ensure_ascii=False))
    for rule in param_rules.load_rules().values():
        for text in (rule.title, rule.source, rule.depends_on, rule.default_text, rule.etalon_where):
            assert not INTERNAL.search(text), (rule.module, rule.field, text)


def test_rule_caption_has_no_question_number():
    for rule in param_rules.load_rules().values():
        assert not re.search(r"В-\d+", param_rules.describe(rule))


@pytest.mark.parametrize("label, short, hint", [
    ("Число скважин", "Число скважин", ""),
    ("Д" * LONG_LABEL, "Д" * LONG_LABEL, ""),
    ("Экспертиза материалов изысканий (т.80 прим.2; К = 1,3 — по листу эталона, B73 «К=1,3»; основание в листе не указано)",
     "Экспертиза материалов изысканий", "т.80 прим.2; К = 1,3 — по листу эталона, B73 «К=1,3»; основание в листе не указано"),
    ("Организация и ликвидация по формуле эталона: коэффициент К — умножает всю сумму оргликвидации в смете раздела",
     "Организация и ликвидация по формуле эталона: коэффициент К", "умножает всю сумму оргликвидации в смете раздела"),
])
def test_split_label(label, short, hint):
    assert split_label(label) == (short, hint)


def test_every_section_label_on_screen_is_short():
    from core.stages import as_staged
    from modules.ecology.module import EcologyModule
    from modules.geodesy.module import GeodesyModule
    from modules.geology.module import GeologyModule
    from modules.geophysics.module import GeophysicsModule
    from modules.hydromet.module import HydrometModule

    longest = 0
    for cls in (GeologyModule, GeophysicsModule, GeodesyModule, EcologyModule, HydrometModule):
        for stage in as_staged(cls()).stages:
            for field in stage.fields:
                longest = max(longest, len(split_label(field.label)[0]))
    assert longest <= LONG_LABEL + 15  # было до 301 знака


def test_split_warnings_keeps_order_and_finds_decisions():
    warnings = ["Состав сметы — позиции листа", "Начисления не применены — ставки", "Отрицательная цена в строке"]
    assert split_warnings(warnings) == (warnings[1:], warnings[:1])


def test_estimate_page_has_few_decisions_and_the_rest_in_a_collapsed_block(isolated_smeta_db):  # noqa: F811
    at = open_app()
    _geology_to_estimate(at)
    assert at.exception == []
    notes = [e for e in at.expander if e.label.startswith("К сведению")]
    assert len(notes) == 1 and notes[0].proto.expanded is False
    outside = [w.value for w in at.warning if w.value not in [n.value for n in notes[0].warning]]
    assert any(w.startswith("Начисления не применены") for w in outside)
    assert len(outside) <= 2
    # таблица сметы — без служебной колонки
    table = next(d.value for d in at.dataframe if "Наименование" in d.value.columns)
    assert "id" not in table.columns


def test_next_step_is_named_after_confirmation(isolated_smeta_db):  # noqa: F811
    at = open_app()
    _geology_to_estimate(at)
    _click(at, "Подтвердить смету")
    assert at.exception == []
    assert any(c.startswith("Дальше:") for c in _texts(at.caption))
    goto(at, "smeta")
    assert any("Дальше: следующий раздел" in c for c in _texts(at.caption))
