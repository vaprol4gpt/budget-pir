import pytest

from core.references import ReferenceBook
from data.build_references import IndexEntry, PriceEntry, build_database


@pytest.fixture
def db_path(tmp_path):
    entries = [
        PriceEntry(
            source="СБЦ-99", table="9", item="3", category=None,
            price=47.2, basis="СБЦ-99 Таблица 9, п.3", name="Рекогносцировка", row=19,
        ),
        PriceEntry(
            source="СБЦ-99", table="17", item="1", category="I кат",
            price=36.0, basis="Таблица 17, п.1", name="Бурение", row=26,
        ),
    ]
    index_entries = [
        IndexEntry(
            work_type="geology", period_start="2026-07-01", period_end="2026-09-30",
            value=81.95, basis="К-81,95 на III квартал 2026 года",
        ),
    ]
    path = tmp_path / "references.sqlite"
    build_database(entries, index_entries, path)
    return path


def test_get_price_found(db_path):
    refs = ReferenceBook(db_path)
    price, basis = refs.get_price("СБЦ-99", "9", "3")
    assert price == pytest.approx(47.2)
    assert "Таблица 9" in basis


def test_get_price_with_category(db_path):
    refs = ReferenceBook(db_path)
    price, _ = refs.get_price("СБЦ-99", "17", "1", category="I кат")
    assert price == pytest.approx(36.0)


def test_get_price_without_category_when_category_required_raises(db_path):
    refs = ReferenceBook(db_path)
    with pytest.raises(KeyError):
        refs.get_price("СБЦ-99", "17", "1")


def test_get_price_wrong_category_raises(db_path):
    refs = ReferenceBook(db_path)
    with pytest.raises(KeyError):
        refs.get_price("СБЦ-99", "17", "1", category="II кат")


def test_get_price_without_category_raises_for_geodesy_duplicated_code(tmp_path):
    # реальная ситуация листа геодезии "№1.1_ИГДИ": Таблица 8 п.3
    # процитирована дважды (строки 21 "1.1" и 47 "4.4") с разной ценой
    # — category=№пп разводит их в справочнике (см.
    # extract_geodesy_price_entries, записи разработки), но
    # запрос БЕЗ category должен явно сигнализировать неоднозначность
    # (KeyError), а не молча вернуть одну из двух цен наугад.
    entries = [
        PriceEntry(
            source="СБЦи5.6", table="8", item="3", category="1.1",
            price=6426.0, basis="Таблица 8 п.3 (полевые)", name="Опорная сеть", row=21,
        ),
        PriceEntry(
            source="СБЦи5.6", table="8", item="3", category="4.4",
            price=2538.0, basis="Таблица 8 п.3 (камеральные)", name="Опорная сеть", row=47,
        ),
    ]
    path = tmp_path / "references.sqlite"
    build_database(entries, [], path)
    refs = ReferenceBook(path)

    with pytest.raises(KeyError):
        refs.get_price("СБЦи5.6", "8", "3")

    assert refs.get_price("СБЦи5.6", "8", "3", category="1.1")[0] == pytest.approx(6426.0)
    assert refs.get_price("СБЦи5.6", "8", "3", category="4.4")[0] == pytest.approx(2538.0)


def test_get_price_unknown_key_raises(db_path):
    refs = ReferenceBook(db_path)
    with pytest.raises(KeyError):
        refs.get_price("СБЦ-99", "999", "1")


def test_missing_db_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        ReferenceBook(tmp_path / "nope.sqlite")


def test_get_index_date_at_period_end(db_path):
    # ровно дата, под которой раньше индекс хранился константой —
    # теперь это конец периода, включительно
    refs = ReferenceBook(db_path)
    assert refs.get_index("geology", "2026-09-30") == pytest.approx(81.95)


def test_get_index_date_inside_period(db_path):
    # сегодняшняя дата на момент постановки задачи (2026-09-18) —
    # не совпадает с границей периода, но попадает внутрь квартала
    refs = ReferenceBook(db_path)
    assert refs.get_index("geology", "2026-09-18") == pytest.approx(81.95)


def test_get_index_date_at_period_start(db_path):
    refs = ReferenceBook(db_path)
    assert refs.get_index("geology", "2026-07-01") == pytest.approx(81.95)


def test_get_index_date_before_period_raises(db_path):
    refs = ReferenceBook(db_path)
    with pytest.raises(KeyError):
        refs.get_index("geology", "2026-06-30")


def test_get_index_date_after_period_raises(db_path):
    refs = ReferenceBook(db_path)
    with pytest.raises(KeyError):
        refs.get_index("geology", "2026-10-01")


def test_get_index_unknown_work_type_raises(db_path):
    refs = ReferenceBook(db_path)
    with pytest.raises(KeyError):
        refs.get_index("geodesy", "2026-09-18")


def test_get_vat_rate(db_path):
    # ставка по дате расчёта (В-16, 30.10.2026): с 01.01.2026 — 22 %
    refs = ReferenceBook(db_path)
    assert refs.get_vat_rate("2026-09-30") == pytest.approx(0.22)


@pytest.fixture
def db_path_with_categories(tmp_path):
    # Отдельная фикстура (не переиспользует db_path выше) — чтобы не
    # задевать другие тесты, которые полагаются на конкретный,
    # ограниченный набор категорий в db_path (например, что "II кат"
    # там заведомо нет).
    entries = [
        PriceEntry(
            source="СБЦ-99", table="17", item="1", category="I кат",
            price=36.0, basis="Таблица 17, п.1", name="Бурение", row=26,
        ),
        PriceEntry(
            source="СБЦ-99", table="17", item="1", category="III кат",
            price=42.6, basis="Таблица 17, п.1", name="Бурение", row=28,
        ),
        PriceEntry(
            source="СБЦ-99", table="17", item="1", category="II кат",
            price=38.4, basis="Таблица 17, п.1", name="Бурение", row=27,
        ),
        PriceEntry(
            source="СБЦ-99", table="9", item="3", category=None,
            price=47.2, basis="СБЦ-99 Таблица 9, п.3", name="Рекогносцировка", row=19,
        ),
    ]
    path = tmp_path / "references_with_categories.sqlite"
    build_database(entries, [], path)
    return path


def test_get_categories_returns_all_categories_for_code(db_path_with_categories):
    refs = ReferenceBook(db_path_with_categories)
    categories = refs.get_categories("СБЦ-99", "17", "1")

    assert categories == [
        ("I кат", pytest.approx(36.0)),
        ("II кат", pytest.approx(38.4)),
        ("III кат", pytest.approx(42.6)),
    ]


def test_get_categories_returns_empty_list_for_unknown_code(db_path_with_categories):
    refs = ReferenceBook(db_path_with_categories)
    assert refs.get_categories("СБЦ-99", "999", "1") == []


def test_get_categories_excludes_rows_without_category(db_path_with_categories):
    # табл.9 п.3 в фикстуре без категории (category=None) — не должна
    # попасть в список категорий даже для своего собственного кода
    refs = ReferenceBook(db_path_with_categories)
    assert refs.get_categories("СБЦ-99", "9", "3") == []
