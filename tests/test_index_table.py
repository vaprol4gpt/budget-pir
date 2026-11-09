"""Этап Г карты (аудит 05.11.2026): индекс нового квартала вносится файлом `data/indeksy.json`,
без правки эталонов; ошибка записи останавливает сборку; индекс старше квартала — строже."""

import json

import pytest

from core.aggregator import BudgetAggregator, ModuleBudget
from core.models import EstimateLine
from core.pricing import calculate_total_with_vat
from core.references import ReferenceBook
from data import build_references as br
from data.build_references import IndexEntry, IndexTableError, PriceEntry, build_database


def write_table(tmp_path, indices):
    path = tmp_path / "indeksy.json"
    path.write_text(json.dumps({"indices": indices}, ensure_ascii=False), encoding="utf-8")
    return path


Q4 = {"work_type": "geology", "period_start": "2026-10-01", "period_end": "2026-12-31",
      "value": 84.0, "source": "письмо Минстроя (тест)"}


def test_project_table_is_valid():
    """Файл проекта читается; значений без документа в нём нет (они — от сметного отдела)."""
    for entry in br.load_index_table():
        assert entry.basis


def test_missing_file_is_an_empty_table(tmp_path):
    assert br.load_index_table(tmp_path / "нет.json") == []


def test_entry_is_read(tmp_path):
    entries = br.load_index_table(write_table(tmp_path, [Q4]))
    assert entries == [IndexEntry("geology", "2026-10-01", "2026-12-31", 84.0, "письмо Минстроя (тест)")]


@pytest.mark.parametrize("change, message", [
    ({"work_type": "geo"}, "неизвестный раздел"),
    ({"period_start": "01.10.2026"}, "ГГГГ-ММ-ДД"),
    ({"period_end": "2026-01-01"}, "начало периода позже"),
    ({"value": "84,0"}, "число больше 0"),
    ({"value": 0}, "число больше 0"),
    ({"source": " "}, "документ-источник"),
])
def test_bad_entry_is_named(tmp_path, change, message):
    with pytest.raises(IndexTableError, match=message):
        br.load_index_table(write_table(tmp_path, [{**Q4, **change}]))


def test_bad_file_is_a_clear_error(tmp_path):
    path = tmp_path / "indeksy.json"
    path.write_text("{не json", encoding="utf-8")
    with pytest.raises(IndexTableError, match="не читается"):
        br.load_index_table(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(IndexTableError, match="indices"):
        br.load_index_table(path)


def test_misplaced_comma_is_caught_against_etalon():
    etalon = [IndexEntry("geology", "2026-07-01", "2026-09-30", 81.95, "эталон")]
    br.check_index_table([IndexEntry("geology", "2026-10-01", "2026-12-31", 84.0, "док")], etalon)
    with pytest.raises(IndexTableError, match="проверьте запись"):
        br.check_index_table([IndexEntry("geology", "2026-10-01", "2026-12-31", 8.4, "док")], etalon)


def _refs(tmp_path, index_entries):
    price = PriceEntry(source="СБЦ-99", table="9", item="3", category=None, price=47.2,
                       basis="СБЦ-99 Таблица 9, п.3", name="Рекогносцировка", row=19)
    path = tmp_path / "references.sqlite"
    build_database([price], index_entries, path)
    return ReferenceBook(path)


ESTIMATE = {"a": EstimateLine("a", "Бурение", "п.м.", 100, 10.0, {}, "A", [], "Т17")}
Q3 = IndexEntry("geology", "2026-07-01", "2026-09-30", 81.95, "К-81,95 на III кв. 2026")


def test_new_quarter_gives_total_without_warning(tmp_path):
    entries = br.load_index_table(write_table(tmp_path, [Q4]))
    refs = _refs(tmp_path, [Q3, *entries])
    totals = calculate_total_with_vat(ESTIMATE, refs, "geology", "2026-11-05")
    assert totals.index == 84.0 and not totals.index_fallback and totals.warnings == []
    assert totals.index_period == "IV кв. 2026"
    # прошлая дата — по-прежнему свой индекс
    assert calculate_total_with_vat(ESTIMATE, refs, "geology", "2026-09-30").index == 81.95


def test_index_one_quarter_old_is_a_plain_warning(tmp_path):
    totals = calculate_total_with_vat(ESTIMATE, _refs(tmp_path, [Q3]), "geology", "2026-11-05")
    assert totals.index_fallback and not totals.index_stale
    assert "больше чем на квартал" not in totals.warnings[0]


def test_index_older_than_a_quarter_is_stricter(tmp_path):
    totals = calculate_total_with_vat(ESTIMATE, _refs(tmp_path, [Q3]), "geology", "2027-02-01")
    assert totals.index_fallback and totals.index_stale
    assert "больше чем на квартал" in totals.warnings[0]
    aggregator = BudgetAggregator()
    aggregator.add_module(ModuleBudget.from_totals("geology", "Геология", totals))
    notes = aggregator.reliability_notes()
    assert len(notes) == 1 and "отстал больше чем на квартал" in notes[0]


def test_table_is_part_of_the_references_fingerprint(tmp_path, monkeypatch):
    path = write_table(tmp_path, [])
    monkeypatch.setattr(br, "INDEX_TABLE_PATH", path)
    before = br.source_fingerprint()
    write_table(tmp_path, [Q4])
    assert br.source_fingerprint() != before


def test_help_does_not_rebuild(capsys, monkeypatch):
    monkeypatch.setattr(br, "build", lambda *a: pytest.fail("--help не должен пересобирать справочник"))
    br.main(["--help"])
    assert "--if-stale" in capsys.readouterr().out
