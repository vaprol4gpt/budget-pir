"""Локальный слой норм компании (С4, 30.10.2026): внутренние ставки текущего
года — только на своём ПК (`data/local/`, в .gitignore), перекрывают
seed-допущения `data/norms/*.json`; нет слоя — работают seed. Данные в
тестах обезличенные (условные числа и источники)."""

import json
from pathlib import Path

import pytest

import core.norms
from core.norms import ORIGIN_LOCAL, ORIGIN_SEED, NormsFormatError, load_norms, local_input_defaults

REAL_LOCAL = Path(__file__).resolve().parent.parent / "data" / "local"


def write_local(folder, section):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / core.norms.LOCAL_FILE).write_text(
        json.dumps({"_формат": 1, "geology_cost": section}, ensure_ascii=False), encoding="utf-8"
    )
    return folder


def test_without_local_layer_seed_values_work(tmp_path):
    norms = load_norms("geology_cost", local_dir=tmp_path)
    assert norms == load_norms("geology_cost", use_local=False)
    assert all(n.origin == ORIGIN_SEED for n in norms.values())
    assert local_input_defaults("geology_cost", local_dir=tmp_path) == {}


def test_local_value_overrides_seed_with_visible_source(tmp_path):
    folder = write_local(tmp_path, {
        "norms": {
            "housing_per_person_month": {"value": 111.0, "source": "Тест!A1", "assumption": True},
            "travel_per_person": {"value": None, "source": ""},  # null — не перекрывать
        },
        "inputs": {
            "salary_worker": {"value": 222.0, "source": "Тест!B2"},
            "salary_itr": {"value": None, "source": ""},
        },
    })
    seed = load_norms("geology_cost", use_local=False)
    norms = load_norms("geology_cost", local_dir=folder)
    housing = norms["housing_per_person_month"]
    assert (housing.value, housing.origin, housing.source) == (111.0, ORIGIN_LOCAL, "локально: Тест!A1")
    assert norms["travel_per_person"] == seed["travel_per_person"]
    defaults = local_input_defaults("geology_cost", local_dir=folder)
    assert list(defaults) == ["salary_worker"]
    assert (defaults["salary_worker"].value, defaults["salary_worker"].source) == (222.0, "Тест!B2")


def test_local_value_without_source_is_error(tmp_path):
    folder = write_local(tmp_path, {"norms": {"travel_per_person": {"value": 1.0}}})
    with pytest.raises(NormsFormatError):
        load_norms("geology_cost", local_dir=folder)


def test_local_layer_newer_format_is_error(tmp_path):
    (tmp_path / core.norms.LOCAL_FILE).write_text('{"_формат": 99}', encoding="utf-8")
    with pytest.raises(NormsFormatError):
        load_norms("geology_cost", local_dir=tmp_path)


def test_example_template_in_git_has_no_values():
    """Шаблон под git — только структура: ни одного значения (ставки компании —
    только в локальном слое)."""
    template = Path(__file__).resolve().parent.parent / "data" / "norms" / "norms_firm.example.json"
    raw = json.loads(template.read_text(encoding="utf-8"))
    for name, groups in raw.items():
        if name.startswith("_"):
            continue
        for section in groups.values():
            assert all(item["value"] is None for item in section.values()), name


def test_minimum_page_uses_local_salaries_and_shows_source(tmp_path, monkeypatch):
    import core.persistence as persistence_module
    from tests.test_streamlit_app import _fill_geology, goto, open_app

    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "smety.sqlite")
    folder = write_local(tmp_path / "local", {
        "inputs": {"salary_worker": {"value": 12345.0, "source": "Тест!C3"}},
    })
    monkeypatch.setattr(core.norms, "LOCAL_DIR", folder)
    at = open_app()
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "minimum")
    assert at.exception == []
    widget = at.number_input(key="cost_salary_worker")
    assert widget.value == 12345.0
    assert "Тест!C3" in (widget.help or "")
    goto(at, "spravochniki")
    assert at.exception == []
    assert any("Тест!C3" in str(df.value.to_dict()) for df in at.dataframe)


def test_broken_local_layer_does_not_break_minimum_page(tmp_path, monkeypatch):
    """С8: битый data/local/norms_firm.json — предупреждение и допущения, без исключения."""
    import core.persistence as persistence_module
    from tests.test_streamlit_app import _fill_geology, goto, open_app

    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "smety.sqlite")
    folder = tmp_path / "local"
    folder.mkdir()
    (folder / core.norms.LOCAL_FILE).write_text("{не json", encoding="utf-8")
    monkeypatch.setattr(core.norms, "LOCAL_DIR", folder)
    at = open_app()
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    for page in ("minimum", "svodny", "spravochniki"):
        goto(at, page)
        assert at.exception == [], page
        assert any("не прочитан" in w.value for w in at.warning), page
