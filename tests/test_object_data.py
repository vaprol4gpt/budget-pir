"""Общие данные объекта и выбор пункта внутреннего транспорта по
расстоянию (Э5 дорожной карты, 25.10.2026)."""

import pytest

from core.object_data import (
    OBJECT_FIELDS,
    CalculationContext,
    ObjectData,
    distance_ranges,
    object_data_of,
    transport_item_by_distance,
)
from core.references import ReferenceBook
from data.build_references import PriceEntry, build_database

TRANSPORT = [
    PriceEntry("СБЦ-99", "4", "3", None, 0.0875, "Таблица 4, п.3", "Расходы по внутреннему транспорту", 151),
    PriceEntry("СБЦ-99", "4", "4", None, 0.1375, "Таблица 4, п.4",
               "Расходы по внутреннему транспорту (от 10 до 15 км)", 20),
    PriceEntry("СБЦ-99", "4", "5", None, 0.1875, "Таблица 4, п.5",
               "Расходы по внутреннему транспорту (от 20 до 25 км)", 21),
]


@pytest.fixture
def refs(tmp_path):
    path = tmp_path / "references.sqlite"
    build_database(TRANSPORT, [], path)
    return ReferenceBook(path)


def test_roundtrip_and_unknown_keys():
    data = ObjectData(code="Т-01", name="ГОК", distance_km=12.0, start_date="2026-06-01",
                      unfavorable_period=True)
    assert ObjectData.from_dict(data.to_dict()) == data
    assert ObjectData.from_dict({"code": "1", "future_field": 5}) == ObjectData(code="1")
    assert ObjectData.from_dict(None).is_empty()


def test_zero_distance_means_not_set():
    assert ObjectData.from_dict({"distance_km": 0}).distance_km is None
    assert ObjectData.from_dict({"distance_km": 0.0}).distance_km is None


def test_object_fields_cover_dataclass():
    assert [f.name for f in OBJECT_FIELDS] == list(ObjectData().to_dict())


def test_warnings():
    assert ObjectData().warnings() == []
    assert any("раньше даты начала" in w
               for w in ObjectData(start_date="2026-07-01", end_date="2026-06-01").warnings())
    assert any("неблагоприятный период" in w and "применяется у геологии" in w
               for w in ObjectData(unfavorable_period=True).warnings())


def test_info_only_filled_fields():
    info = ObjectData(code="Т-01", distance_km=12.0, start_date="2026-06-01",
                      unfavorable_period=True).info()
    assert info == {
        "Шифр объекта": "Т-01",
        "Расстояние от базы до объекта, км": "12.0",
        "Дата начала работ": "01.06.2026",
        "Работы в неблагоприятный период": "да",
    }


def test_context_proxies_refs_and_carries_object_data(refs):
    ctx = CalculationContext(refs, ObjectData(code="X"))
    assert ctx.get_price("СБЦ-99", "4", "4")[0] == pytest.approx(0.1375)
    assert object_data_of(ctx).code == "X"
    assert object_data_of(refs) == ObjectData()


def test_distance_ranges_from_reference_names(refs):
    assert distance_ranges(refs, "СБЦ-99", "4") == [(10.0, 15.0, "4"), (20.0, 25.0, "5")]


@pytest.mark.parametrize(
    "distance, item, warned",
    [(10, "4", False), (12.5, "4", False), (15, "4", False), (20, "5", False), (25, "5", False),
     (7, "3", True), (17, "3", True), (40, "3", True), (None, "3", True)],
)
def test_transport_item_by_distance(refs, distance, item, warned):
    ctx = CalculationContext(refs, ObjectData(distance_km=distance))
    choice = transport_item_by_distance(ctx, "СБЦ-99", "4", default_item="3")
    assert choice.item == item
    assert (choice.warning is not None) == warned


def test_transport_without_list_items_falls_back_to_default():
    class FakeRefs:
        def get_price(self, *args, **kwargs):
            raise KeyError

    ctx = CalculationContext(FakeRefs(), ObjectData(distance_km=12))
    choice = transport_item_by_distance(ctx, "СБЦ-99", "4", default_item="3")
    assert choice.item == "3" and "диапазоны: нет" in choice.warning


# --- данные объекта в модулях геологии и экологии, хранение (Э5) ---

from core.persistence import SmetaStore  # noqa: E402
from core.stages import StagedSession, as_staged  # noqa: E402
from modules.ecology.module import EcologyModule  # noqa: E402
from modules.geology.module import GeologyModule  # noqa: E402

GEOLOGY_VOLUMES = {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}],
                   "drilling_count": 30}


@pytest.fixture
def real_refs():
    from core.references import DEFAULT_DB_PATH

    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    return ReferenceBook()


def _geology_estimate(refs):
    session = StagedSession(as_staged(GeologyModule()))
    session.inputs["volumes"] = GEOLOGY_VOLUMES
    session.confirm("volumes", refs)
    return session.compute("estimate", refs)


def test_geology_transport_by_distance(real_refs):
    draft = _geology_estimate(CalculationContext(real_refs, ObjectData(distance_km=22)))
    transport = draft.effective["geology.transport"]
    assert transport.coefs["rate"] == pytest.approx(0.1875)
    assert "по расстоянию 22 км" in transport.basis
    assert not any("Внутренний транспорт" in w for w in draft.warnings)


def test_geology_without_distance_keeps_etalon_item_with_warning(real_refs):
    plain = _geology_estimate(real_refs)
    with_empty_object = _geology_estimate(CalculationContext(real_refs, ObjectData()))
    transport = plain.effective["geology.transport"]
    assert transport.coefs["rate"] == pytest.approx(0.0875)
    assert "расстоянию" not in transport.basis
    assert plain.effective == with_empty_object.effective
    assert any("расстояние до объекта не задано" in w and "п.3" in w for w in plain.warnings)


def test_geology_distance_outside_ranges_keeps_etalon_item_with_warning(real_refs):
    draft = _geology_estimate(CalculationContext(real_refs, ObjectData(distance_km=40)))
    assert draft.effective["geology.transport"].coefs["rate"] == pytest.approx(0.0875)
    assert any("для расстояния 40 км" in w for w in draft.warnings)


def test_ecology_transport_by_distance(real_refs):
    module = EcologyModule()
    data = {"radiation_area": 10, "radon_points": 0, "physical_factors_points": 0,
            "sampling": [], "data_acquisition_count": 0}
    base = module.build_estimate(module.build_volumes(data, real_refs), real_refs)
    far = module.build_estimate(
        module.build_volumes(data, real_refs), CalculationContext(real_refs, ObjectData(distance_km=22))
    )
    assert base["ecology.transport"].coefs["rate"] == pytest.approx(0.1375)  # п.4 эталонов
    assert far["ecology.transport"].coefs["rate"] == pytest.approx(0.1875)  # п.5


def test_version_and_draft_keep_object_data(tmp_path):
    from core.stages import SOURCE_INPUT, VolumeLine, make_result

    store = SmetaStore(tmp_path / "smety.sqlite")
    result = make_result("volumes", {"w": VolumeLine("w", "w", "шт", 1, SOURCE_INPUT)}, {}, [], {})
    data = ObjectData(code="Т-01", distance_km=12.0).to_dict()
    version = store.save_version("ГОК", "geology", None, result, object_data=data)
    bare = store.save_version("ГОК", "geology", version, result)
    assert store.get_version_object_data(version) == data
    assert store.get_version_object_data(bare) is None
    draft_id = store.save_draft("ГОК", "geology", "volumes", {}, [], version, object_data=data)
    assert store.load_draft(draft_id).object_data == data


def test_schema_v2_database_gets_object_data_columns(tmp_path):
    import sqlite3

    path = tmp_path / "smety.sqlite"
    SmetaStore(path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE smeta_versions DROP COLUMN object_data")
    conn.execute("ALTER TABLE drafts DROP COLUMN object_data")
    conn.execute("UPDATE schema_version SET version = 2")
    conn.commit()
    conn.close()

    SmetaStore(path)
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(smeta_versions)")}
    draft_columns = {row[1] for row in conn.execute("PRAGMA table_info(drafts)")}
    version = conn.execute("SELECT version FROM schema_version").fetchall()
    conn.close()
    assert "object_data" in columns and "object_data" in draft_columns
    from core.persistence import SCHEMA_VERSION
    assert version == [(SCHEMA_VERSION,)]


def test_non_numeric_saved_values_are_skipped_with_warning():
    """Ревью сводной №3: битое значение в сохранённых данных не роняет чтение;
    запятая — десятичный разделитель."""
    from core.object_data import ObjectData

    data = ObjectData.from_dict({
        "price_reductions": {"budget": "0,8", "geology": "abc", "ecology": 0.65},
        "accrual_overrides": {"geology": {"regional_field_rate": "x", "org_liquidation_k": "2,5"}},
        "transport_distance_overrides": {"ecology": "12,5", "geology": [1]},
    })
    assert data.price_reductions == {"budget": 0.8, "ecology": 0.65}
    assert data.accrual_overrides == {"geology": {"org_liquidation_k": 2.5}}
    assert data.transport_distance_overrides == {"ecology": 12.5}
    warnings = data.load_warnings()
    assert len(warnings) == 3
    assert any("«abc»" in w for w in warnings)
    assert all(w in data.warnings() for w in warnings)
    # предупреждения чтения — не данные: не сохраняются и не мешают сравнению
    assert "_load_warnings" not in data.to_dict()
    assert data == ObjectData.from_dict(data.to_dict())


def test_broken_table_shapes_do_not_fail():
    from core.object_data import ObjectData

    data = ObjectData.from_dict({"price_reductions": "0.8", "accrual_overrides": [1, 2]})
    assert data.price_reductions == {} and data.accrual_overrides == {}


def test_scalar_numbers_from_saved_data_are_parsed_or_skipped():
    """Итоговое ревью прогона 30.10 (№2, №3): «12,5» в сохранённых данных объекта
    понимается, нечисловое и NaN/бесконечность пропускаются с предупреждением,
    а не роняют открытие версии."""
    from core.object_data import ObjectData

    data = ObjectData.from_dict({"distance_km": "12,5", "regional_field_rate": "0,5",
                                 "org_liquidation_k": "abc", "external_transport_rate": "inf"})
    assert data.distance_km == 12.5 and data.regional_field_rate == 0.5
    assert data.org_liquidation_k is None and data.external_transport_rate is None
    assert len(data.load_warnings()) == 2
    data.warnings()  # не падает на сравнении строки с числом
    nan = ObjectData.from_dict({"transport_distance_overrides": {"geology": "nan"}})
    assert nan.transport_distance_overrides == {}
