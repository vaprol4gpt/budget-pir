"""Дата расчёта и индексы вне периода (Э1 дорожной карты, 25.10.2026).

Индексы в справочнике действуют квартал (сейчас только III кв. 2026);
с 01.10.2026 на дату «сегодня» индекса нет. Проверяется: последний
известный индекс с предупреждением (политика по умолчанию), строгий
режим (прежнее поведение), выбор записи при пересекающихся периодах,
UNIQUE периода в справочнике, дата расчёта в версии сметы и сквозной
итог на 2026-09-30 — контрольная цифра 28 680 880,97 руб. (НДС 22 %). История:
27 983 420,18 — по ошибочной для 2026 г. ставке НДС 20 %; 28 449 810,51 — НДС 22 %;
28 680 880,97 — после доводки геологии до листа «1.2.1_ИГИ» (Э6а, 30.10.2026,
записи разработки)."""

import sqlite3

import pytest

from core.models import EstimateLine
from core.persistence import SmetaStore
from core.pricing import INDEX_POLICY_STRICT, calculate_total_with_vat
from core.references import DEFAULT_DB_PATH, IndexRecord, ReferenceBook
from core.stages import SOURCE_INPUT, VolumeLine, make_result
from data.build_references import IndexEntry, PriceEntry, build_database

PRICE = PriceEntry(
    source="СБЦ-99", table="9", item="3", category=None,
    price=47.2, basis="СБЦ-99 Таблица 9, п.3", name="Рекогносцировка", row=19,
)


def make_refs(tmp_path, index_entries):
    path = tmp_path / "references.sqlite"
    conflicts = build_database([PRICE], index_entries, path)
    return ReferenceBook(path), conflicts


Q3 = IndexEntry("geology", "2026-07-01", "2026-09-30", 81.95, "К-81,95 на III кв. 2026")
Q2 = IndexEntry("geology", "2026-04-01", "2026-06-30", 80.0, "К-80 на II кв. 2026")


@pytest.fixture
def estimate():
    return {
        "a": EstimateLine("a", "Бурение", "п.м.", 100, 10.0, {}, "A", [], "Т17"),
    }


# --- справочник ---


def test_last_index_is_latest_period_before_date(tmp_path):
    refs, _ = make_refs(tmp_path, [Q2, Q3])
    last = refs.get_last_index("geology", "2026-10-25")
    assert last.value == pytest.approx(81.95)
    assert (last.period_start, last.period_end) == ("2026-07-01", "2026-09-30")


def test_last_index_before_all_periods_raises(tmp_path):
    refs, _ = make_refs(tmp_path, [Q3])
    with pytest.raises(KeyError):
        refs.get_last_index("geology", "2026-06-30")


def test_overlapping_periods_take_latest_period_end(tmp_path):
    # уточнённое письмо на август–октябрь поверх квартального индекса:
    # дата внутри обоих периодов — берётся запись с более поздним концом
    overlap = IndexEntry("geology", "2026-08-01", "2026-10-31", 83.0, "уточнение")
    refs, _ = make_refs(tmp_path, [overlap, Q3])
    assert refs.get_index("geology", "2026-08-15") == pytest.approx(83.0)
    assert refs.get_index("geology", "2026-07-15") == pytest.approx(81.95)


def test_duplicate_index_period_same_value_written_once(tmp_path):
    refs, conflicts = make_refs(tmp_path, [Q3, Q3])
    assert conflicts == []
    assert refs.get_index("geology", "2026-08-01") == pytest.approx(81.95)


def test_duplicate_index_period_different_value_is_conflict(tmp_path):
    other = IndexEntry("geology", "2026-07-01", "2026-09-30", 90.0, "другое письмо")
    refs, conflicts = make_refs(tmp_path, [Q3, other])
    assert len(conflicts) == 1 and "неоднозначный индекс" in conflicts[0]
    with pytest.raises(KeyError):
        refs.get_index("geology", "2026-08-01")


def test_price_indices_has_unique_period(tmp_path):
    refs, _ = make_refs(tmp_path, [Q3])
    conn = sqlite3.connect(tmp_path / "references.sqlite")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO price_indices VALUES (?, ?, ?, ?, ?)",
                ("geology", "2026-07-01", "2026-09-30", 1.0, "x"),
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "start, end, label",
    [
        ("2026-07-01", "2026-09-30", "III кв. 2026"),
        ("2026-10-01", "2026-12-31", "IV кв. 2026"),
        ("2026-01-01", "2026-03-31", "I кв. 2026"),
        ("2026-08-01", "2026-10-31", "01.08.2026–31.10.2026"),
    ],
)
def test_period_label(start, end, label):
    assert IndexRecord(1.0, start, end, "").period_label() == label


# --- итог с индексацией ---


def test_date_inside_period_no_warning(tmp_path, estimate):
    refs, _ = make_refs(tmp_path, [Q3])
    totals = calculate_total_with_vat(estimate, refs, "geology", "2026-09-30")
    assert totals.warnings == []
    assert not totals.index_fallback
    assert totals.index_period == "III кв. 2026"
    assert totals.total_with_vat == pytest.approx(1000 * 81.95 * 1.22)


def test_date_after_period_uses_last_index_with_warning(tmp_path, estimate):
    refs, _ = make_refs(tmp_path, [Q2, Q3])
    totals = calculate_total_with_vat(estimate, refs, "geology", "2026-10-25")
    assert totals.index_fallback
    assert totals.index == pytest.approx(81.95)
    assert totals.total_with_vat == pytest.approx(1000 * 81.95 * 1.22)
    assert len(totals.warnings) == 1
    assert "III кв. 2026" in totals.warnings[0]
    assert "25.10.2026" in totals.warnings[0]
    assert "не внесён" in totals.warnings[0]


def test_strict_policy_gives_no_total(tmp_path, estimate):
    refs, _ = make_refs(tmp_path, [Q3])
    totals = calculate_total_with_vat(
        estimate, refs, "geology", "2026-10-25", index_policy=INDEX_POLICY_STRICT
    )
    assert totals.index is None and totals.total_with_vat is None
    assert not totals.index_fallback
    assert totals.base_total == pytest.approx(1000)
    assert "не найден" in totals.warnings[0]


def test_date_before_all_periods_gives_no_total_even_last_known(tmp_path, estimate):
    refs, _ = make_refs(tmp_path, [Q3])
    totals = calculate_total_with_vat(estimate, refs, "geology", "2026-06-01")
    assert totals.total_with_vat is None
    assert not totals.index_fallback


def test_unknown_policy_raises(tmp_path, estimate):
    refs, _ = make_refs(tmp_path, [Q3])
    with pytest.raises(ValueError):
        calculate_total_with_vat(estimate, refs, "geology", "2026-09-30", index_policy="guess")


# --- дата расчёта в версии сметы ---


def _result():
    lines = {"w": VolumeLine("w", "Работа", "шт", 1, SOURCE_INPUT, "ввод")}
    return make_result("volumes", lines, {"n": 1}, [], {})


def test_version_keeps_calc_date(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    with_date = store.save_version("ГОК", "geology", None, _result(), calc_date="2026-09-30")
    without = store.save_version("ГОК", "geology", with_date, _result())
    assert store.get_version_calc_date(with_date) == "2026-09-30"
    assert store.get_version_calc_date(without) is None
    with pytest.raises(KeyError):
        store.get_version_calc_date(999)


def test_old_database_without_calc_date_column_is_extended(tmp_path):
    # база в формате до Э1: smeta_versions без calc_date, одна версия
    path = tmp_path / "smety.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE smeta_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, "
        "name TEXT NOT NULL, module_code TEXT NOT NULL, stage_key TEXT NOT NULL, "
        "inputs TEXT NOT NULL, edits TEXT NOT NULL, lines TEXT NOT NULL, "
        "fingerprint TEXT NOT NULL, based_on TEXT NOT NULL, confirmed_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO smeta_versions (parent_id, name, module_code, stage_key, inputs, edits, "
        "lines, fingerprint, based_on, confirmed_at) VALUES "
        "(NULL, 'старая', 'geology', 'volumes', '{}', '[]', '[]', 'x', '{}', '2026-09-01')"
    )
    conn.commit()
    conn.close()

    store = SmetaStore(path)
    assert store.get_version_calc_date(1) is None
    assert store.load_version_chain(1)[1] == "старая"
    new_id = store.save_version("новая", "geology", None, _result(), calc_date="2026-10-25")
    assert store.get_version_calc_date(new_id) == "2026-10-25"


# --- сквозной прогон на дату (контрольная цифра) ---

needs_real_refs = pytest.mark.skipif(
    not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite (запустите build_references.py)"
)


@needs_real_refs
def test_run_budget_stub_on_2026_09_30_gives_control_total():
    import run_budget_stub
    from core.aggregator import BudgetAggregator

    aggregator = run_budget_stub.build_budget(BudgetAggregator(), ReferenceBook(), "2026-09-30")
    summary = aggregator.summary()
    assert round(summary["total_with_vat_sum"], 2) == 28680880.97
    assert all(m.warnings == [] for m in summary["modules"])
    # итог без НДС: 23 319 516,82 (одинаков при НДС 20 % и 22 % — смена ставки
    # тронула только НДС), 23 508 918,82 — после Э6а геологии
    indexed = sum(m.indexed_total for m in summary["modules"])
    assert round(indexed, 2) == 23508918.82
    assert summary["total_with_vat_sum"] == pytest.approx(indexed * 1.22, abs=1e-6)


@needs_real_refs
def test_run_budget_stub_after_period_gives_same_total_with_warnings():
    import run_budget_stub
    from core.aggregator import BudgetAggregator

    aggregator = run_budget_stub.build_budget(BudgetAggregator(), ReferenceBook(), "2026-10-25")
    summary = aggregator.summary()
    assert round(summary["total_with_vat_sum"], 2) == 28680880.97
    assert summary["excluded_from_total"] == []
    assert all(len(m.warnings) == 1 for m in summary["modules"])


@needs_real_refs
def test_run_budget_stub_strict_after_period_excludes_all():
    import run_budget_stub
    from core.aggregator import BudgetAggregator

    aggregator = run_budget_stub.build_budget(
        BudgetAggregator(), ReferenceBook(), "2026-10-25", INDEX_POLICY_STRICT
    )
    summary = aggregator.summary()
    assert summary["total_with_vat_sum"] == 0
    assert len(summary["excluded_from_total"]) == 5


def test_run_budget_stub_rejects_bad_date():
    import run_budget_stub

    with pytest.raises(ValueError):
        run_budget_stub.parse_args(["--date", "25.10.2026"])
