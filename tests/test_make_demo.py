"""Демо-база tools/make_demo.py: два объекта, все разделы эталонов, оба этапа
подтверждены, итоги совпадают с эталонами в допусках эталонных тестов."""

import pytest

from core.persistence import SmetaStore
from core.references import DEFAULT_DB_PATH
from tools import make_demo

pytestmark = [
    pytest.mark.skipif(not (make_demo.ROAD_XLSX.exists() and make_demo.KARIER_XLSX.exists()),
                       reason="нет эталона в reference_data/"),
    pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite"),
]


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "smety.sqlite"
    path.write_bytes(b"old")  # существующий файл пересоздаётся
    saved = make_demo.build_demo(path)
    return path, saved


def test_two_objects_with_their_sections(demo_db):
    path, saved = demo_db
    assert set(saved) == {make_demo.ROAD_NAME, make_demo.KARIER_NAME}
    assert set(saved[make_demo.ROAD_NAME]) == set(make_demo.LABELS)        # пять разделов
    # у Карьера нет листа геофизики в эталоне — четыре раздела
    assert set(saved[make_demo.KARIER_NAME]) == set(make_demo.LABELS) - {"geophysics"}
    store = SmetaStore(path)
    rows = store.list_versions()
    assert len(rows) == 2 * (5 + 4)  # ведомость + смета на раздел
    assert {r[2] for r in rows} == {make_demo.ROAD_NAME, make_demo.KARIER_NAME}


def test_versions_are_chains_volumes_then_estimate_with_object_and_date(demo_db):
    path, saved = demo_db
    store = SmetaStore(path)
    codes = {make_demo.ROAD_NAME: "Т-01", make_demo.KARIER_NAME: "Т-02"}
    for name, versions in saved.items():
        for code, version_id in versions.items():
            module_code, chain_name, results = store.load_version_chain(version_id)
            assert (module_code, chain_name) == (code, name)
            assert [r.stage_key for r in results] == ["volumes", "estimate"]
            assert store.get_version_calc_date(version_id) == make_demo.DATE
            data = store.get_version_object_data(version_id)
            assert data["code"] == codes[name] and data["customer"] == ""


def test_read_back_confirmed_and_recomputes_the_same(demo_db):
    path, _saved = demo_db
    for name in (make_demo.ROAD_NAME, make_demo.KARIER_NAME):
        rows, _obj, errors = make_demo.read_back(path, name)
        assert errors == []  # оба этапа подтверждены, пересчёт = снимок
        assert all(r.total_with_vat > 0 for r in rows)


def test_totals_match_etalons_within_test_tolerances(demo_db):
    path, _saved = demo_db
    checks, budgets, errors = make_demo.expected_checks(path)
    assert errors == []
    assert all(c.ok for items in checks.values() for c in items)
    for budget in budgets.values():
        assert budget["vat_amount_sum"] == pytest.approx(budget["reduced_total"] * 0.22, rel=1e-9)


def test_main_returns_zero_and_prints_table(tmp_path, capsys):
    assert make_demo.main([str(tmp_path / "demo.sqlite")]) == 0
    out = capsys.readouterr().out
    assert "Сверка с эталонами — в допусках." in out
    assert "Демо — Автодорога" in out and "Демо — Карьер" in out
