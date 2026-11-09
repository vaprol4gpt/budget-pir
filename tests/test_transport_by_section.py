"""Расстояние для внутреннего транспорта по разделу (боевой прогон 31.10.2026,
внутренняя проверка): в сметах компании разделы одного
объекта берут разные пункты СБЦ-99 т.4 — у геологии и экологии может быть своё
расстояние, перекрывающее общее расстояние объекта."""

import pytest

from core.object_data import (
    CalculationContext,
    ObjectData,
    transport_item_by_distance,
)
from core.references import ReferenceBook
from core.stages import StagedSession, as_staged
from data.build_references import PriceEntry, build_database
from modules.ecology.module import EcologyModule
from modules.geology.module import GeologyModule

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


@pytest.fixture
def real_refs():
    from core.references import DEFAULT_DB_PATH

    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    return ReferenceBook()


# --- данные объекта ---

def test_section_distance_overrides_object_distance():
    data = ObjectData(distance_km=12.0, transport_distance_overrides={"ecology": 22.0})
    assert data.transport_distance("ecology") == (22.0, "расстояние раздела «Экология»")
    assert data.transport_distance("geology") == (12.0, "общее расстояние объекта")
    assert data.transport_distance(None) == (12.0, "общее расстояние объекта")


def test_from_dict_keeps_only_known_sections_and_set_values():
    data = ObjectData.from_dict({"transport_distance_overrides": {
        "geology": 0, "ecology": 22, "geodesy": 5, "hydromet": None}})
    assert data.transport_distance_overrides == {"ecology": 22.0}
    form = ObjectData.from_dict({"transport_distance_overrides": {"geology": None, "ecology": None}})
    assert form.transport_distance_overrides == {} and form.is_empty()


def test_old_data_without_field_opens_and_roundtrips():
    old = {"code": "Т-01", "distance_km": 12.0}  # сохранено до появления поля
    data = ObjectData.from_dict(old)
    assert data.transport_distance_overrides == {}
    full = ObjectData(distance_km=12.0, transport_distance_overrides={"geology": 3.0})
    assert ObjectData.from_dict(full.to_dict()) == full


def test_info_and_warnings():
    data = ObjectData(transport_distance_overrides={"geology": 12.0, "ecology": -1.0})
    assert data.info()["Расстояние для внутреннего транспорта по разделам"] == (
        "Геология: 12 км; Экология: -1 км")
    assert any("Экология" in w and "отрицательное" in w for w in data.warnings())
    assert "Расстояние для внутреннего транспорта по разделам" not in ObjectData(code="1").info()


@pytest.mark.parametrize(
    "overrides, distance, geology_item, ecology_item",
    [
        ({}, 12.0, "4", "4"),                                  # как раньше — общее расстояние
        ({"geology": 12.0, "ecology": 22.0}, None, "4", "5"),  # только по разделам
        ({"ecology": 22.0}, 12.0, "4", "5"),                   # раздел перекрывает общее
    ],
)
def test_choice_by_section(refs, overrides, distance, geology_item, ecology_item):
    ctx = CalculationContext(refs, ObjectData(distance_km=distance,
                                              transport_distance_overrides=overrides))
    assert transport_item_by_distance(ctx, "СБЦ-99", "4", "3", module="geology").item == geology_item
    assert transport_item_by_distance(ctx, "СБЦ-99", "4", "3", module="ecology").item == ecology_item


def test_note_names_origin(refs):
    ctx = CalculationContext(refs, ObjectData(distance_km=12.0,
                                              transport_distance_overrides={"ecology": 22.0}))
    own = transport_item_by_distance(ctx, "СБЦ-99", "4", "3", module="ecology")
    common = transport_item_by_distance(ctx, "СБЦ-99", "4", "3", module="geology")
    assert "расстояние раздела «Экология»" in own.note
    assert "общее расстояние объекта" in common.note


# --- модули геологии и экологии одного объекта ---

GEOLOGY_VOLUMES = {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}],
                   "drilling_count": 30}
ECOLOGY_DATA = {"radiation_area": 10, "radon_points": 0, "physical_factors_points": 0,
                "sampling": [], "data_acquisition_count": 0}


def _geology_transport(ctx):
    session = StagedSession(as_staged(GeologyModule()))
    session.inputs["volumes"] = GEOLOGY_VOLUMES
    session.confirm("volumes", ctx)
    return session.compute("estimate", ctx).effective["geology.transport"]


def _ecology_transport(ctx):
    module = EcologyModule()
    return module.build_estimate(module.build_volumes(ECOLOGY_DATA, ctx), ctx)["ecology.transport"]


def test_geology_and_ecology_of_one_object_take_different_items(real_refs):
    ctx = CalculationContext(real_refs, ObjectData(
        distance_km=12.0, transport_distance_overrides={"geology": 12.0, "ecology": 22.0}))
    geology, ecology = _geology_transport(ctx), _ecology_transport(ctx)
    assert geology.coefs["rate"] == pytest.approx(0.1375)  # п.4
    assert ecology.coefs["rate"] == pytest.approx(0.1875)  # п.5
    assert "расстояние раздела «Геология»" in geology.basis
    assert "расстояние раздела «Экология»" in ecology.basis


def test_without_section_distance_as_before(real_refs):
    plain = CalculationContext(real_refs, ObjectData(distance_km=22.0))
    empty = CalculationContext(real_refs, ObjectData(distance_km=22.0, transport_distance_overrides={}))
    assert _geology_transport(plain) == _geology_transport(empty)
    assert _geology_transport(plain).coefs["rate"] == pytest.approx(0.1875)
    assert _ecology_transport(plain).coefs["rate"] == pytest.approx(0.1875)
    assert "общее расстояние объекта" in _ecology_transport(plain).basis


def test_object_2026_pattern_geology_p3_ecology_p5(real_refs):
    """Как на объекте 2026 г.: геология — п.3, экология — п.5. У п.3 в
    справочнике нет диапазона расстояний — геология остаётся на пункте эталона
    (расстояние не задано), экология берёт п.5 по своему расстоянию."""
    ctx = CalculationContext(real_refs, ObjectData(transport_distance_overrides={"ecology": 22.0}))
    assert _geology_transport(ctx).coefs["rate"] == pytest.approx(0.0875)  # п.3
    assert _ecology_transport(ctx).coefs["rate"] == pytest.approx(0.1875)  # п.5
