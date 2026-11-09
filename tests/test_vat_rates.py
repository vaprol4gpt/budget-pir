"""Ставка НДС по дате расчёта (список вопросов В-16, закрыт 30.10.2026).

Ставка — не константа, а периоды с основанием в справочнике (таблица
vat_rates, data/build_references.py::VAT_RATES): до 31.12.2025 — 20 %, с
01.01.2026 — 22 % (425-ФЗ). Проверяется: граница периодов, подпись с
основанием, расчёт итога по дате, справочник старой сборки без таблицы
(итог без НДС с подсказкой, не падение) и внешняя сверка — правило НДС
совпадает с формулой эталонного листа «1.2.1_ИГИ» до копейки."""

import sqlite3
from pathlib import Path

import pytest

from core.models import EstimateLine
from core.pricing import calculate_total_with_vat
from core.references import ReferenceBook
from data.build_references import VAT_RATES, IndexEntry, PriceEntry, build_database

PRICE = PriceEntry(
    source="СБЦ-99", table="9", item="3", category=None,
    price=47.2, basis="СБЦ-99 Таблица 9, п.3", name="Рекогносцировка", row=19,
)
# Индекс на стыке годов, чтобы итог считался по обе стороны границы НДС.
INDEX = IndexEntry("geology", "2025-10-01", "2026-03-31", 80.0, "тестовый индекс")


@pytest.fixture
def refs(tmp_path):
    path = tmp_path / "references.sqlite"
    build_database([PRICE], [INDEX], path)
    return ReferenceBook(path)


@pytest.fixture
def estimate():
    return {"a": EstimateLine("a", "Бурение", "п.м.", 100, 10.0, {}, "A", [], "Т17")}


@pytest.mark.parametrize(
    "day, rate",
    [("2019-01-01", 0.20), ("2025-12-31", 0.20), ("2026-01-01", 0.22), ("2026-09-30", 0.22),
     ("2035-01-01", 0.22)],
)
def test_rate_by_calc_date(refs, day, rate):
    assert refs.get_vat_rate(day) == pytest.approx(rate)


def test_date_before_known_periods_is_not_guessed(refs):
    # до 2019 г. ставка была 18 % — период не внесён, 20 % не подставляем
    with pytest.raises(KeyError):
        refs.get_vat_rate("2018-12-31")


def test_label_names_rate_and_basis(refs):
    assert refs.get_vat_entry("2026-01-01").label() == (
        "НДС 22 % — 425-ФЗ от 28.11.2025 (НК РФ ст. 164 п. 3), с 01.01.2026"
    )
    assert refs.get_vat_entry("2025-12-31").label().startswith("НДС 20 % — НК РФ ст. 164 п. 3")
    assert refs.get_vat_entry("2025-12-31").label().endswith("01.01.2019–31.12.2025")


def test_all_vat_rates_listed_in_order(refs):
    assert [(r.period_start, r.rate) for r in refs.all_vat_rates()] == [
        ("2019-01-01", 0.20), ("2026-01-01", 0.22),
    ]


def test_vat_periods_do_not_overlap_and_have_no_gaps():
    from datetime import date, timedelta

    periods = sorted((e.period_start, e.period_end) for e in VAT_RATES)
    for (_, end), (next_start, _) in zip(periods, periods[1:]):
        assert date.fromisoformat(end) + timedelta(days=1) == date.fromisoformat(next_start)


def test_total_uses_rate_of_calc_date(refs, estimate):
    before = calculate_total_with_vat(estimate, refs, "geology", "2025-12-31")
    after = calculate_total_with_vat(estimate, refs, "geology", "2026-01-01")
    # индекс один и тот же — итог без НДС одинаковый, меняется только НДС
    assert before.indexed_total == after.indexed_total == pytest.approx(1000 * 80.0)
    assert before.total_with_vat == pytest.approx(1000 * 80.0 * 1.20)
    assert after.total_with_vat == pytest.approx(1000 * 80.0 * 1.22)
    assert before.vat_label.startswith("НДС 20 %")
    assert after.vat_label.startswith("НДС 22 % — 425-ФЗ")


def test_old_reference_db_without_vat_table_gives_total_without_vat(refs, estimate, tmp_path):
    # справочник, собранный до 30.10.2026: таблицы vat_rates нет —
    # итог с индексацией есть, НДС нет, в предупреждении — как пересобрать
    conn = sqlite3.connect(tmp_path / "references.sqlite")
    conn.execute("DROP TABLE vat_rates")
    conn.commit()
    conn.close()
    totals = calculate_total_with_vat(estimate, refs, "geology", "2026-01-01")
    assert totals.indexed_total == pytest.approx(80000.0)
    assert totals.vat_rate is None and totals.total_with_vat is None
    assert any("build_references.py" in w for w in totals.warnings)
    assert refs.all_vat_rates() == []


ETALON = Path(__file__).resolve().parent.parent / "reference_data" / "Автодорога_эталон.xlsx"


@pytest.mark.skipif(not ETALON.exists(), reason="нет эталонной книги в reference_data/")
def test_etalon_geology_sheet_vat_matches_our_rule(refs):
    """Внешняя проверка: на листе «1.2.1_ИГИ» подпись «НДС, 20 %», а формула
    H161 =PRODUCT(H160,0.22) — наша ставка на дату индекса эталона (III кв.
    2026) и наше правило (без округления) дают ровно H161 и H162."""
    import openpyxl

    ws = openpyxl.load_workbook(ETALON, data_only=True)["1.2.1_ИГИ"]
    indexed, vat, total = ws["H160"].value, ws["H161"].value, ws["H162"].value
    rate = refs.get_vat_rate("2026-09-30")
    assert round(indexed * rate, 2) == vat
    assert round(indexed + indexed * rate, 2) == total
