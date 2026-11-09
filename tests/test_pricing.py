import pytest

from core.models import EstimateLine
from core.pricing import calculate_total_with_vat


class FakeReferenceBook:
    """Двойник ReferenceBook для теста pricing без обращения к SQLite.

    index/vat_rate — либо число (успех), либо None (имитирует
    отсутствие данных на дату: get_index/get_vat_rate бросают
    KeyError, как настоящий ReferenceBook)."""

    def __init__(self, index=None, vat_rate=None):
        self._index = index
        self._vat_rate = vat_rate

    def get_index(self, work_type, date):
        if self._index is None:
            raise KeyError(f"индекс не найден на {date}")
        return self._index

    def get_vat_rate(self, date):
        if self._vat_rate is None:
            raise KeyError(f"ставка НДС не найдена на {date}")
        return self._vat_rate


@pytest.fixture
def estimate():
    line = EstimateLine(
        id="geology.drilling.IV",
        name="Бурение",
        unit="п.м.",
        qty=300,
        price=45.6,
        coefs={},
        kind="A",
        base_ids=[],
        basis="Таблица 17, п.1",
    )
    return {line.id: line}  # base_total = 300 * 45.6 = 13680.0


def test_calculate_total_with_vat_applies_index_and_vat(estimate):
    refs = FakeReferenceBook(index=2.0, vat_rate=0.20)

    result = calculate_total_with_vat(estimate, refs, work_type="geology", date="2026-09-18")

    assert result.base_total == pytest.approx(13680.0)
    assert result.index == pytest.approx(2.0)
    assert result.indexed_total == pytest.approx(27360.0)
    assert result.vat_rate == pytest.approx(0.20)
    assert result.vat_amount == pytest.approx(5472.0)
    assert result.total_with_vat == pytest.approx(32832.0)
    assert result.warnings == []


def test_calculate_total_with_vat_missing_index_gives_partial_result(estimate):
    refs = FakeReferenceBook(index=None, vat_rate=0.20)

    result = calculate_total_with_vat(estimate, refs, work_type="geology", date="2026-09-18")

    assert result.base_total == pytest.approx(13680.0)
    assert result.index is None
    assert result.indexed_total is None
    assert result.vat_rate is None
    assert result.vat_amount is None
    assert result.total_with_vat is None
    assert len(result.warnings) == 1
    assert "Индекс" in result.warnings[0]
    assert "2026-09-18" in result.warnings[0]


def test_calculate_total_with_vat_missing_vat_rate_after_index_found(estimate):
    refs = FakeReferenceBook(index=2.0, vat_rate=None)

    result = calculate_total_with_vat(estimate, refs, work_type="geology", date="2026-09-18")

    assert result.base_total == pytest.approx(13680.0)
    assert result.index == pytest.approx(2.0)
    assert result.indexed_total == pytest.approx(27360.0)  # индексация всё же применилась
    assert result.vat_rate is None
    assert result.vat_amount is None
    assert result.total_with_vat is None  # без ставки НДС итог с НДС недоступен
    assert len(result.warnings) == 1
    assert "НДС" in result.warnings[0]


def test_calculate_total_with_vat_does_not_raise_when_both_missing(estimate):
    refs = FakeReferenceBook(index=None, vat_rate=None)

    result = calculate_total_with_vat(estimate, refs, work_type="geology", date="2026-09-18")

    assert result.base_total == pytest.approx(13680.0)
    assert result.total_with_vat is None
    assert len(result.warnings) == 1  # до НДС дело не доходит, т.к. индекса уже нет
