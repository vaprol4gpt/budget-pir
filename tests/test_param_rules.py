"""Справочник правил выбора параметров (`data/pravila_parametrov.json`, `core/param_rules.py`;
прогон 03.11.2026, раздел 1.3): правила согласованы с полями модулей; модули берут значения по
умолчанию из справочника (ответ специалиста — правка одной записи, без кода); допущения
считаются в смете и в сводном бюджете; изменение без обоснования — предупреждение."""

import dataclasses
import json

import pytest

import core.persistence as persistence_module
from app import ui
from core import param_rules
from core.object_data import OBJECT_FIELDS
from core.stages import StagedSession, as_staged
from modules.geology.module import VK_PROGRAMMA_RABOT, GeologyModule
from tests.test_streamlit_app import _click, _geology_to_estimate, _texts, goto, open_app


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


@pytest.fixture
def rules_file(tmp_path, monkeypatch):
    """Копия справочника правил во временной папке: тест правит её, как правил бы человек."""
    data = json.loads(param_rules.RULES_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "pravila.json"

    def write(changes=None):
        for rule in data["rules"]:
            for (module, field), values in (changes or {}).items():
                if rule["module"] == module and rule["field"] == field:
                    rule.update(values)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        param_rules._load_rules.cache_clear()
        return path

    monkeypatch.setattr(param_rules, "RULES_PATH", path)
    write()
    yield write
    param_rules._load_rules.cache_clear()


def estimate_fields(code):
    module = ui.MODULES[code]["class"]()
    return {f.name: f for stage in module.stages() if stage.key != "volumes" for f in stage.fields}


def test_rules_file_loads_and_statuses_are_known():
    rules = param_rules.load_rules()
    assert len(rules) >= 30
    assert {r.status for r in rules.values()} <= set(param_rules.STATUS_LABELS)
    # у каждого допущения — вопрос к специалисту, у каждой записи — источник
    for r in rules.values():
        assert r.source, r
        if r.is_assumption:
            assert r.question, r


def test_every_estimate_rule_is_a_real_field_and_defaults_agree():
    object_names = {f.name for f in OBJECT_FIELDS}
    for (module, field), rule in param_rules.load_rules().items():
        if rule.scope == param_rules.SCOPE_OBJECT:
            assert field in object_names, (module, field)
            continue
        fields = estimate_fields(module)
        assert field in fields, (module, field)
        if rule.default is not None and fields[field].type == "number":
            # поле формы показывает значение справочника правил
            assert fields[field].default == pytest.approx(rule.default), (module, field)


def test_one_edit_of_rules_file_changes_the_calculation(rules_file):
    """Ответ нач. ОИГ «к = 1,25 к программе работ — норма» — правка одной записи файла."""
    def programma_coef():
        session = StagedSession(as_staged(GeologyModule()))
        session.inputs["volumes"] = {
            "drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}],
            "drilling_count": 30,
        }
        refs = ui.ReferenceBook()
        session.confirm("volumes", refs)
        session.inputs["estimate"] = {}  # оператор параметр не трогал
        line = session.compute("estimate", refs).effective[f"geology.{VK_PROGRAMMA_RABOT}"]
        return line.coefs.get("k")

    assert programma_coef() is None  # по умолчанию 1 — коэффициент не применяется
    rules_file({("geology", "k_programma_rabot"): {"default": 1.25, "status": "norm",
                                                  "source": "ответ нач. ОИГ (тест)"}})
    assert programma_coef() == pytest.approx(1.25)
    assert estimate_fields("geology")["k_programma_rabot"].default == pytest.approx(1.25)


def test_assumptions_changed_and_without_reason():
    fields = list(estimate_fields("geology"))
    assert len(param_rules.assumptions("geology", {}, fields)) == 3
    inputs = {"k_programma_rabot": 1.25, "k_monolity_skalnye": 0.7,
              param_rules.REASONS_KEY: {"k_programma_rabot": "указание нач. ОИГ"}}
    uses = {u.rule.field: u.kind for u in param_rules.param_uses("geology", inputs, fields)}
    assert uses == {
        "k_programma_rabot": param_rules.USE_CHANGED,
        "k_monolity_skalnye": param_rules.USE_NO_REASON,
        "k_rekognoscirovka": param_rules.USE_ASSUMPTION,
    }
    # правила другого этапа не считаются (на ведомости полей сметы нет)
    assert param_rules.assumptions("geology", {}, ["drilling_count"]) == []


def test_broken_rules_file_is_a_clear_error(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"rules": [{"module": "geology", "field": "x", "scope": "estimate",
                                           "title": "x", "status": "maybe"}]}), encoding="utf-8")
    with pytest.raises(param_rules.ParamRulesError, match="статус"):
        param_rules.load_rules(path)


def test_estimate_page_shows_rules_reason_and_assumptions(isolated_smeta_db):
    at = open_app()
    _geology_to_estimate(at)
    assert at.exception == []
    assert any(c.startswith("Правило: допущение — ждёт ответа") for c in _texts(at.caption))
    assert any("Итог раздела содержит 3 допущения" in w for w in _texts(at.warning))
    # изменение значения — поле обоснования и предупреждение, пока оно пустое
    at.number_input(key="field_geology_estimate_k_programma_rabot").set_value(1.25)
    at.run(timeout=30)
    assert any("изменено без обоснования" in w for w in _texts(at.warning))
    reason = at.text_input(key="field_geology_estimate_k_programma_rabot__reason")
    reason.set_value("указание нач. ОИГ от 01.11")
    at.run(timeout=30)
    assert not any("изменено без обоснования" in w for w in _texts(at.warning))
    assert any("Итог раздела содержит 2 допущения" in w for w in _texts(at.warning))
    session = at.session_state["staged_sessions"]["geology"]
    assert session.inputs["estimate"][param_rules.REASONS_KEY] == {
        "k_programma_rabot": "указание нач. ОИГ от 01.11"
    }

    # подтверждённая смета → в сводном бюджете «итог содержит N допущений»
    _click(at, "Подтвердить смету")
    goto(at, "svodny")
    assert at.exception == []
    # + допущение уровня объекта: пункт внутреннего транспорта по расстоянию
    assert any("Итог содержит 3 допущения в параметрах смет: Геология (ОИГ) — 2; данные объекта — 1" in w
               for w in _texts(at.warning))
    table = next(d.value for d in at.dataframe if "Итого с НДС" in d.value.columns)
    assert list(table[ui.ASSUMPTIONS_COLUMN]) == [2, 3]
    # раздел без начислений в сводной виден: и в предупреждении, и в колонке
    assert any("Геология (ОИГ): Начисления не применены" in w for w in _texts(at.warning))
    assert table[ui.ACCRUALS_COLUMN].iloc[0] == "не все заданы"


def test_references_page_lists_rules(isolated_smeta_db):
    at = open_app("spravochniki")
    assert at.exception == []
    table = next(d.value for d in at.dataframe if "Параметр" in d.value.columns)
    assert len(table) == len(param_rules.load_rules())
    assert "допущение — ждёт ответа" in set(table["Статус"])


def test_rule_dataclass_is_frozen():
    rule = next(iter(param_rules.load_rules().values()))
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.default = 2


def test_zero_in_number_field_without_program_value_is_not_a_change():
    """Ревью 03.11, №1: районный к техотчёту ИГМИ без значения программы форма показывает как
    0 — это «не задано», а не изменение без обоснования."""
    fields = list(estimate_fields("hydromet"))
    uses = {u.rule.field: u.kind for u in param_rules.param_uses(
        "hydromet", {"report_regional_coefficient": 0.0}, fields)}
    assert uses["report_regional_coefficient"] == param_rules.USE_ASSUMPTION
    uses = {u.rule.field: u.kind for u in param_rules.param_uses(
        "hydromet", {"report_regional_coefficient": 1.25}, fields)}
    assert uses["report_regional_coefficient"] == param_rules.USE_NO_REASON


def test_broken_rules_file_falls_back_to_module_values(tmp_path, monkeypatch, isolated_smeta_db):
    """Ревью 03.11, №4: битый файл правил не останавливает программу — разделы работают на
    прежних значениях, ошибка видна на странице «Справочники»."""
    path = tmp_path / "broken.json"
    path.write_text("{ это не JSON", encoding="utf-8")
    monkeypatch.setattr(param_rules, "RULES_PATH", path)
    param_rules._load_rules.cache_clear()
    try:
        assert param_rules.default("geology", "k_programma_rabot", 1.0) == 1.0
        assert param_rules.rule("geology", "k_programma_rabot") is None
        assert "не читается" in param_rules.load_error()
        at = open_app("spravochniki")
        assert at.exception == []
        assert any("Справочник правил параметров не читается" in e for e in _texts(at.error))
    finally:
        param_rules._load_rules.cache_clear()


def test_default_text_names_the_choice():
    item = param_rules.rule("hydromet", "unfavorable_period_mode")
    assert param_rules.default_label(item) == "не применять"
    assert "по умолчанию не применять" in param_rules.describe(item)


def test_rules_with_program_value_are_assumptions_and_match_the_modules():
    """Аудит 05.11.2026, С-2: параметр, который молча берёт значение программы, — допущение,
    а не «ввод оператора»; значение в справочнике = значение модуля."""
    from modules.ecology import module as ecology

    for (module, field), rule in param_rules.load_rules().items():
        if rule.status == param_rules.STATUS_INPUT and rule.scope == param_rules.SCOPE_ESTIMATE:
            assert rule.default is None, (module, field)
    assert param_rules.rule("ecology", "subbase_item").default == ecology.SUBBASE_ITEM_DEFAULT
    assert param_rules.rule("ecology", "passability").default == ecology.PASSABILITY_DEFAULT


def test_object_assumptions_depend_on_sections_and_rates():
    from core.object_data import ObjectData

    plain = ObjectData()
    assert param_rules.object_assumptions(plain, ["hydromet"]) == []
    assert [r.field for r in param_rules.object_assumptions(plain, ["geology"])] == ["distance_km"]
    assert [r.field for r in param_rules.object_assumptions(plain, ["geodesy"])] == []
    with_org = ObjectData(org_liquidation_rate=0.06)
    assert [r.field for r in param_rules.object_assumptions(with_org, ["geodesy"])] == ["org_liquidation_k"]
    # у ИГМИ начисления свои: общая ставка объекта на неё не влияет
    assert param_rules.object_assumptions(with_org, ["hydromet"]) == []


# --- аудит 05.11.2026, О-8: ошибка в файле правил — понятный отказ, а не падение и не миллиарды ---


def _write_rules(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "rules.json"
    path.write_text(text, encoding=encoding)
    return path


def _one_rule(default):
    return json.dumps({"rules": [{"module": "geology", "field": "k", "scope": "estimate", "title": "к",
                                  "status": "assumption", "source": "тест", "question": "В-1",
                                  "default": default}]}, ensure_ascii=False)


@pytest.mark.parametrize("text, message", [
    ("[1, 2]", "ключом «rules»"),
    ('{"rules": {"a": 1}}', "ключом «rules»"),
    ('{"rules": [1]}', "ожидается объект"),
    (_one_rule("1,25"), "с запятой"),
    (_one_rule(125), "вне пределов"),
    (_one_rule(-1), "вне пределов"),
    (_one_rule([1]), "недопустимый тип"),
])
def test_bad_rules_file_is_a_clear_error(tmp_path, text, message):
    with pytest.raises(param_rules.ParamRulesError, match=message):
        param_rules.load_rules(_write_rules(tmp_path, text))


def test_nan_default_is_refused(tmp_path):
    text = _one_rule(1.0).replace("1.0", "NaN")
    with pytest.raises(param_rules.ParamRulesError, match="не число"):
        param_rules.load_rules(_write_rules(tmp_path, text))


def test_rules_file_saved_with_bom_is_read(tmp_path):
    rules = param_rules.load_rules(_write_rules(tmp_path, _one_rule(1.25), encoding="utf-8-sig"))
    assert rules[("geology", "k")].default == 1.25


@pytest.mark.parametrize("text", ["[1, 2]", "{не json", _one_rule(125)])
def test_program_works_on_old_values_with_a_bad_rules_file(tmp_path, monkeypatch, text):
    monkeypatch.setattr(param_rules, "RULES_PATH", _write_rules(tmp_path, text))
    param_rules._load_rules.cache_clear()
    try:
        assert param_rules.default("geology", "k_programma_rabot", 1.0) == 1.0
        assert param_rules.rules_for("geology") == []
        assert param_rules.load_error()
    finally:
        param_rules._load_rules.cache_clear()


def test_numeric_parameter_never_gets_text_from_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(param_rules, "RULES_PATH", _write_rules(tmp_path, _one_rule("1.25")))
    param_rules._load_rules.cache_clear()
    try:
        assert param_rules.default("geology", "k", 1.0) == 1.0
    finally:
        param_rules._load_rules.cache_clear()


def test_bad_rules_file_is_named_in_the_budget(isolated_smeta_db, tmp_path, monkeypatch):
    monkeypatch.setattr(param_rules, "RULES_PATH", _write_rules(tmp_path, "{не json"))
    param_rules._load_rules.cache_clear()
    try:
        at = open_app()
        _geology_to_estimate(at)
        assert at.exception == []
        assert any("Справочник правил параметров не читается" in e for e in _texts(at.error))
        _click(at, "Подтвердить смету")
        goto(at, "svodny")
        assert any("число допущений неизвестно" in w for w in _texts(at.warning))
    finally:
        param_rules._load_rules.cache_clear()
