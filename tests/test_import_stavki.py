"""Шаблон «Ставки себестоимости» и его импорт в локальный слой норм
(`tools/make_stavki_template.py`, `tools/import_stavki.py`, 03.11.2026).

Числа — выдуманные, нарочно непохожие на реальные ставки; шаблон заполняется
программно во временной папке."""

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

import core.norms
from app.ui import COST_FIELDS
from core.cost import COMMON_INPUT_KEYS, INPUT_ALIASES, cost_input_defaults, load_cost_norms
from core.norms import ORIGIN_LOCAL, load_norms, local_input_defaults
from tools.import_stavki import BACKUP_PREFIX, run_import
from tools.make_stavki_template import (
    ALL_ARTICLES,
    EXCLUDED_NORMS,
    HEADERS,
    LABEL_WHO,
    NORM_FILES,
    TEMPLATE_PATH,
    UNUSED_TITLE,
    build_workbook,
)

SEED = load_norms("geology_cost", use_local=False)


def key_rows(ws):
    """Ключ → номер строки таблицы ставок."""
    return {row[1].value: row[1].row for row in ws.iter_rows(min_col=1, max_col=2)
            if row[1].value in {a.key for a in ALL_ARTICLES}}


def fill(tmp_path, values, who="Тестов Т.Т., бухгалтер", name="filled.xlsx", edit=None):
    """Шаблон с заполненными строками: ключ → (значение, источник[, комментарий])."""
    wb = build_workbook()
    ws = wb.active
    rows = key_rows(ws)
    for r in ws.iter_rows(min_col=1, max_col=1):
        if r[0].value == LABEL_WHO:
            ws.cell(row=r[0].row, column=2, value=who)
    for key, cells in values.items():
        for offset, value in enumerate(cells):
            ws.cell(row=rows[key], column=4 + offset, value=value)
    if edit:
        edit(ws, rows)
    path = tmp_path / name
    wb.save(path)
    return path


def test_template_keys_match_code():
    """Шаблон не устарел (все разделы, 03.11.2026): каждая норма каждого файла
    data/norms/*.json — в шаблоне (в своём разделе локального слоя; общие
    нормы, которые геология хранит у себя по истории, — в общих) или в
    исключениях с причиной; поля ввода шаблона — настоящие поля страницы
    (геологии или общие); оклады и цены топлива геологии — в шаблоне сами или
    через общую должность; единицы норм шаблона = единицы норм кода."""
    norm_articles = {(a.target, a.key) for a in ALL_ARTICLES if a.section == "norms"}
    common_keys = set(load_norms("cost_common", use_local=False))
    for name in NORM_FILES:
        for key in load_norms(name, use_local=False):
            covered = ((name, key) in norm_articles or (name, key) in EXCLUDED_NORMS
                       or (name == "geology_cost" and key in common_keys
                           and (("cost_common", key) in norm_articles
                                or ("cost_common", key) in EXCLUDED_NORMS)))
            assert covered, (name, key)
    assert not norm_articles & set(EXCLUDED_NORMS)
    fields = {name for name, _label, _kind in COST_FIELDS} | COMMON_INPUT_KEYS
    input_keys = {a.key for a in ALL_ARTICLES if a.section == "inputs"}
    assert input_keys <= fields
    aliases = INPUT_ALIASES["geology_cost"]
    for f in {name for name, _l, _k in COST_FIELDS if name.startswith(("salary_", "fuel_price_"))}:
        assert f in input_keys or aliases.get(f) in input_keys, f
    for article in ALL_ARTICLES:
        if article.section == "norms" and article.scale == 1:
            seed = load_cost_norms(article.target, use_local=False)
            assert article.unit == seed[article.key].unit, article.key


def test_template_in_git_matches_generator():
    ws = load_workbook(TEMPLATE_PATH).active
    generated = build_workbook().active
    assert [[c for c in r] for r in ws.iter_rows(values_only=True)] == \
        [[c for c in r] for r in generated.iter_rows(values_only=True)]
    assert ws.protection.sheet
    rows = key_rows(ws)
    assert set(rows) == {a.key for a in ALL_ARTICLES}
    row = next(iter(rows.values()))
    assert ws.cell(row=row, column=2).protection.locked  # ключ заблокирован
    assert ws.cell(row=row, column=3).protection.locked  # единица заблокирована
    for col in (4, 5, 6):
        assert not ws.cell(row=row, column=col).protection.locked
    # значения в шаблоне под git пусты
    assert all(ws.cell(row=r, column=4).value is None for r in rows.values())


def test_import_values_visible_to_load_norms(tmp_path):
    path = fill(tmp_path, {
        "housing_per_person_month": (12345.67, "Тест-приказ №0", "проверка"),
        "social_contributions_rate": (31.7, "Тест-письмо №0"),
        "salary_worker": (77777.7, "Тест-штатное №0"),
        "fuel_price_diesel": (66.6, "Тест-счёт №0"),
    })
    local = tmp_path / "local"
    report = run_import(path, local / core.norms.LOCAL_FILE)
    assert report.errors == [] and report.written
    assert len(report.taken) == 4
    # общие ставки — в разделе "cost_common" и видны геологии и остальным разделам
    for name in ("geology_cost", "geodesy_cost", "hydromet_cost"):
        norms = load_cost_norms(name, local_dir=local)
        housing = norms["housing_per_person_month"]
        assert (housing.value, housing.origin, housing.source) == \
            (12345.67, ORIGIN_LOCAL, "локально: Тест-приказ №0")
        assert norms["social_contributions_rate"].value == pytest.approx(0.317)  # проценты → доля
        assert norms["travel_per_person"].value == SEED["travel_per_person"].value  # пустое не перекрывает
    defaults = cost_input_defaults("geology_cost", local_dir=local)
    assert defaults["salary_worker"].value == 77777.7
    assert defaults["fuel_price_diesel"].source == "Тест-счёт №0"
    assert cost_input_defaults("ecology_cost", local_dir=local)["fuel_price_diesel"].value == 66.6
    raw = json.loads((local / core.norms.LOCAL_FILE).read_text(encoding="utf-8"))
    assert "housing_per_person_month" not in raw["geology_cost"].get("norms", {})
    assert raw["geology_cost"]["inputs"]["salary_worker"]["value"] == 77777.7
    entry = raw["cost_common"]["norms"]["housing_per_person_month"]
    assert entry["imported"] and entry["filled_by"] == "Тестов Т.Т., бухгалтер"
    assert entry["note"] == "проверка"


@pytest.mark.parametrize("cells, needle", [
    ((-5, "Тест"), "отрицательное"),
    (("много", "Тест"), "не число"),
    ((12.3, None), "источник"),
])
def test_bad_values_are_errors_and_nothing_written(tmp_path, cells, needle):
    path = fill(tmp_path, {"travel_per_person": cells})
    out = tmp_path / "local" / core.norms.LOCAL_FILE
    report = run_import(path, out)
    assert any(needle in e for e in report.errors), report.errors
    assert not out.exists()


def test_wrong_unit_is_error(tmp_path):
    def edit(ws, rows):
        ws.protection.sheet = False
        ws.cell(row=rows["travel_per_person"], column=3, value="тыс. руб./чел.")
    path = fill(tmp_path, {"travel_per_person": (1.5, "Тест")}, edit=edit)
    report = run_import(path, tmp_path / "out.json")
    assert any("единица" in e for e in report.errors)
    assert not (tmp_path / "out.json").exists()


def test_comma_text_number_is_accepted(tmp_path):
    path = fill(tmp_path, {"communication_per_person_month": ("1 234,5", "Тест")})
    report = run_import(path, tmp_path / "out.json")
    assert report.errors == []
    assert report.taken[0].value == 1234.5


def test_outlier_is_warning_not_block(tmp_path):
    seed = SEED["external_lab_per_sample"].value
    path = fill(tmp_path, {"external_lab_per_sample": (seed * 25, "Тест"),
                           "drilling_tools_per_pm": (99999, "Тест")})
    report = run_import(path, tmp_path / "out.json")
    assert report.errors == [] and report.written
    assert any("Сторонние лабораторные" in w and "больше" in w for w in report.warnings)
    assert any("архив" in w for w in report.warnings)


def test_existing_file_not_overwritten_without_force(tmp_path):
    out = tmp_path / core.norms.LOCAL_FILE
    before = {"_формат": 1, "geology_cost": {"norms": {
        "travel_per_person": {"value": 4321.0, "source": "Тест-старый"}}}}
    out.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    path = fill(tmp_path, {"housing_per_person_month": (5555.5, "Тест")})
    report = run_import(path, out)
    assert any("--force" in e for e in report.errors)
    assert json.loads(out.read_text(encoding="utf-8")) == before
    dry = run_import(path, out, dry_run=True)
    assert dry.errors == [] and not dry.written
    assert any("housing_per_person_month" in c for c in dry.changes)
    assert json.loads(out.read_text(encoding="utf-8")) == before
    assert not list(tmp_path.glob(f"{BACKUP_PREFIX}*"))


def test_force_makes_backup_and_merges(tmp_path):
    out = tmp_path / core.norms.LOCAL_FILE
    before = {"_формат": 1, "geology_cost": {
        "norms": {"travel_per_person": {"value": 4321.0, "source": "Тест-старый"},
                  "housing_per_person_month": {"value": 1.0, "source": "Тест-старый"}},
        "inputs": {"salary_cook": {"value": 3333.0, "source": "Тест-старый"}}}}
    out.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    path = fill(tmp_path, {"housing_per_person_month": (5555.5, "Тест-новый")})
    report = run_import(path, out, force=True)
    assert report.errors == [] and report.written
    backups = list(tmp_path.glob(f"{BACKUP_PREFIX}*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == before
    norms = load_cost_norms("geology_cost", local_dir=tmp_path)
    assert norms["housing_per_person_month"].value == 5555.5
    assert norms["travel_per_person"].value == 4321.0  # нет в шаблоне (пусто) — сохранено
    assert local_input_defaults("geology_cost", local_dir=tmp_path)["salary_cook"].value == 3333.0
    # прежняя запись геологии общей ставки убрана (иначе перекрыла бы ответ бухгалтерии)
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert "housing_per_person_month" not in raw["geology_cost"]["norms"]
    assert raw["cost_common"]["norms"]["housing_per_person_month"]["value"] == 5555.5
    assert any(c.startswith("− geology_cost.housing_per_person_month") for c in report.changes)


def test_unused_articles_reported_not_imported(tmp_path):
    def edit(ws, rows):
        title = next(r[0].row for r in ws.iter_rows(min_col=1, max_col=1) if r[0].value == UNUSED_TITLE)
        ws.cell(row=title + 2, column=4, value=42)
        ws.cell(row=title + 2, column=5, value="Тест")
    path = fill(tmp_path, {}, edit=edit)
    report = run_import(path, tmp_path / "out.json")
    assert report.errors == [] and report.taken == []
    assert len(report.unused_filled) == 1
    raw = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert raw["geology_cost"] == {}


def test_not_a_template_is_error(tmp_path):
    from openpyxl import Workbook
    path = tmp_path / "other.xlsx"
    Workbook().save(path)
    report = run_import(path, tmp_path / "out.json")
    assert report.errors and not report.written
    assert HEADERS[0] in report.errors[0] or "таблицы" in report.errors[0]


def test_default_output_is_local_layer(tmp_path, monkeypatch):
    """Путь по умолчанию — LOCAL_DIR (в тестах подменён на временную папку)."""
    monkeypatch.setattr(core.norms, "LOCAL_DIR", tmp_path / "loc")
    path = fill(tmp_path, {"travel_per_person": (2468.0, "Тест")})
    report = run_import(path)
    assert report.out_path == tmp_path / "loc" / core.norms.LOCAL_FILE and report.written
    assert Path(report.out_path).exists()


def test_imported_template_marks_rates_as_accounting_for_minimum(tmp_path):
    """Сквозная связка импорт → минимум (прогон 03.11): все ключевые ставки из шаблона
    бухгалтерии — и расчёт минимума показывает «Ставки компании от бухгалтерии» с датой."""
    from modules.geology.cost import CostInputs, rates_status

    path = fill(tmp_path, {
        "salary_worker": (51111.1, "Тест-штатное №1"), "salary_itr": (52222.2, "Тест-штатное №1"),
        "salary_field_driver": (53333.3, "Тест-штатное №1"), "salary_cook": (54444.4, "Тест-штатное №1"),
        "salary_office_engineer": (55555.5, "Тест-штатное №1"),
        "field_allowance_per_person_month": (1111.1, "Тест-приказ №2"),
        "housing_per_person_month": (2222.2, "Тест-приказ №2"),
        "travel_per_person": (3333.3, "Тест-приказ №2"),
    })
    local = tmp_path / "local"
    report = run_import(path, local / core.norms.LOCAL_FILE)
    assert report.errors == [] and report.written
    raw = json.loads((local / core.norms.LOCAL_FILE).read_text(encoding="utf-8"))
    entry = raw["geology_cost"]["inputs"]["salary_worker"]
    assert entry["kind"] == core.norms.KIND_ACCOUNTING and len(entry["date"]) == 10
    norms = load_cost_norms("geology_cost", local_dir=local)
    # водитель и камеральщик геологии — из общих должностей (core/cost.py::INPUT_ALIASES)
    defaults = cost_input_defaults("geology_cost", local_dir=local)
    assert defaults["salary_driver"].value == 53333.3 and defaults["salary_office"].value == 55555.5
    inputs = CostInputs.from_dict(dict(  # общие поля геологии не нужны — from_dict их отбросит
        brigades=2, itr=1, drivers=1, cooks=1, office_staff=2, office_months=1,
        **{k: v.value for k, v in defaults.items() if k.startswith("salary_")}))
    status = rates_status(inputs, norms, defaults)
    assert status.from_accounting
    assert status.text().startswith("Ставки компании от бухгалтерии")
