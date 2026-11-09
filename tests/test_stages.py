"""core/stages.py — этапы, снимки, отметка "устарел", адаптер для
одноэтапных модулей и пилот (геология, ведомость → смета)."""

import dataclasses

import pytest

from core.models import EstimateLine
from core.module_base import Field
from core.overrides import Edit
from core.references import ReferenceBook
from core.stages import (
    SOURCE_CLASS_A,
    SOURCE_CLASS_B,
    SOURCE_CLASS_V,
    SOURCE_INPUT,
    SOURCE_LAB,
    SOURCE_OPERATOR,
    STATUS_CONFIRMED,
    STATUS_DRAFT,
    STATUS_STALE,
    Stage,
    StagedModule,
    StagedSession,
    StageNotReady,
    VolumeLine,
    as_staged,
    fingerprint,
    lines_from_json,
    lines_to_json,
)
from modules.ecology.module import EcologyModule
from modules.geodesy.module import GeodesyModule
from modules.geology.module import GeologyModule
from modules.geophysics.module import GeophysicsModule

GEOLOGY_INPUT = {
    "drilling_by_category": [
        {"soil_category": "II", "drilling_length": 172},
        {"soil_category": "IV", "drilling_length": 602},
    ],
    "drilling_count": 30,
}


@pytest.fixture(scope="module")
def refs():
    return ReferenceBook()


# --- игрушечный двухэтапный модуль: общий механизм без предметки ---


class ToyModule:
    """Этап 1 — объём = ввод "n"; этап 2 — смета объём × цена, цена —
    ПАРАМЕТР этапа 2 (решение №12: параметры сметы — ввод этапа сметы)."""

    code = "toy"

    def field_options(self, field_name, refs):
        return []

    def stages(self):
        def volumes(inputs, previous, refs, pinned):
            n = inputs.get("n", 0)
            qty = pinned.get("work", {}).get("qty", n)
            source = SOURCE_OPERATOR if "work" in pinned else SOURCE_INPUT
            return {"work": VolumeLine("work", "Работа", "шт", qty, source)}

        def estimate(inputs, previous, refs, pinned):
            qty = previous["volumes"].lines["work"].qty
            return {
                "toy.work": EstimateLine(
                    "toy.work", "Работа", "шт", qty, inputs.get("price", 0), {}, "A", [], "",
                )
            }

        return [
            Stage(1, "volumes", "Ведомость", [Field("n", "N", "number", True)], volumes,
                  editable_fields=lambda line, prev: ("qty",)),
            Stage(2, "estimate", "Смета", [Field("price", "Цена", "number", True)], estimate,
                  editable_fields=lambda line, prev: ("price",)),
        ]


def toy_session(n=10, price=5):
    session = StagedSession(as_staged(ToyModule()))
    session.inputs["volumes"] = {"n": n}
    session.inputs["estimate"] = {"price": price}
    return session


def test_staged_module_validates_numbers_and_keys():
    stage = ToyModule().stages()[0]
    with pytest.raises(ValueError):
        StagedModule(ToyModule(), [])
    with pytest.raises(ValueError):
        StagedModule(ToyModule(), [dataclasses.replace(stage, number=2)])
    with pytest.raises(ValueError):
        StagedModule(ToyModule(), [stage, dataclasses.replace(stage, number=2)])


def test_later_stage_cannot_be_computed_before_earlier_is_confirmed():
    session = toy_session()
    with pytest.raises(StageNotReady):
        session.compute("estimate", refs=None)
    with pytest.raises(StageNotReady):
        session.confirm("estimate", refs=None)


def test_stage_two_uses_its_own_input_parameters():
    session = toy_session(n=10, price=7)
    session.confirm("volumes", refs=None)
    assert session.current == "estimate"
    line = session.compute("estimate", refs=None).effective["toy.work"]
    assert (line.qty, line.price) == (10, 7)


def test_snapshot_is_immutable_and_independent_of_later_input_changes():
    session = toy_session(n=10)
    snapshot = session.confirm("volumes", refs=None)
    session.inputs["volumes"]["n"] = 99

    assert session.result("volumes").lines["work"].qty == 10
    with pytest.raises(TypeError):
        snapshot.lines["work"] = None
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.lines["work"].qty = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.is_stale = True
    assert snapshot.confirmed_at
    assert snapshot.fingerprint == fingerprint(snapshot.lines)


def test_reopening_earlier_stage_marks_later_stale_without_deleting_it():
    session = toy_session()
    session.confirm("volumes", refs=None)
    estimate_snapshot = session.confirm("estimate", refs=None)
    assert session.last_stage_status() == STATUS_CONFIRMED

    session.reopen("volumes")
    assert session.current == "volumes"
    assert session.status("volumes") == STATUS_DRAFT
    assert session.status("estimate") == STATUS_STALE
    stale = session.result("estimate")
    assert stale.is_stale is True
    assert stale.fingerprint == estimate_snapshot.fingerprint
    # сам снимок не изменился — флаг только в выданной копии
    assert estimate_snapshot.is_stale is False
    # пока ведомость на доработке, смету посчитать нельзя
    with pytest.raises(StageNotReady):
        session.compute("estimate", refs=None)


def test_reconfirming_unchanged_earlier_stage_makes_later_fresh_again():
    session = toy_session()
    session.confirm("volumes", refs=None)
    session.confirm("estimate", refs=None)
    session.reopen("volumes")
    session.confirm("volumes", refs=None)
    assert session.status("estimate") == STATUS_CONFIRMED


def test_reconfirming_changed_earlier_stage_keeps_later_stale_until_reconfirmed():
    session = toy_session(n=10)
    session.confirm("volumes", refs=None)
    session.confirm("estimate", refs=None)
    session.reopen("volumes")
    session.inputs["volumes"]["n"] = 12
    session.confirm("volumes", refs=None)

    assert session.status("volumes") == STATUS_CONFIRMED
    assert session.status("estimate") == STATUS_STALE
    assert session.result("estimate").lines["toy.work"].qty == 10  # прежний снимок

    session.confirm("estimate", refs=None)
    assert session.status("estimate") == STATUS_CONFIRMED
    assert session.result("estimate").lines["toy.work"].qty == 12


def test_each_stage_has_its_own_override_log():
    session = toy_session()
    session.overrides["volumes"].apply("work", "qty", 10, 11)
    session.confirm("volumes", refs=None)
    session.overrides["estimate"].apply("toy.work", "price", 5, 6)

    assert len(session.overrides["volumes"].history()) == 1
    assert len(session.overrides["estimate"].history()) == 1
    session.overrides["estimate"].undo()
    assert len(session.overrides["volumes"].history()) == 1
    assert session.result("volumes").lines["work"].qty == 11


def test_edits_that_no_longer_apply_are_listed_not_dropped():
    session = toy_session()
    session.confirm("volumes", refs=None)
    log = session.overrides["estimate"]
    log.apply("toy.work", "qty", 10, 20)  # qty на этапе сметы не правится
    log.apply("toy.gone", "price", 1, 2)  # такой строки нет
    log.apply("toy.work", "price", 5, 8)

    draft = session.compute("estimate", refs=None)
    assert draft.effective["toy.work"].qty == 10
    assert draft.effective["toy.work"].price == 8
    assert [(e.line_id, e.field) for e in draft.not_applied] == [
        ("toy.work", "qty"), ("toy.gone", "price"),
    ]
    assert len(log.history()) == 3  # журнал не тронут


def test_restore_rebuilds_session_from_snapshots():
    session = toy_session(n=10, price=5)
    session.overrides["volumes"].apply("work", "qty", 10, 11, "по факту")
    first = session.confirm("volumes", refs=None)
    second = session.confirm("estimate", refs=None)

    restored = StagedSession(as_staged(ToyModule()))
    restored.restore([first, second])
    assert restored.last_stage_status() == STATUS_CONFIRMED
    assert restored.inputs["volumes"] == {"n": 10}
    assert restored.overrides["volumes"].history()[0].reason == "по факту"
    assert restored.current == "estimate"

    partial = StagedSession(as_staged(ToyModule()))
    partial.restore([first])
    assert partial.current == "estimate"
    assert partial.status("estimate") == STATUS_DRAFT


def test_lines_json_roundtrip_keeps_fingerprint():
    lines = {
        "work": VolumeLine("work", "Работа", "шт", 2.5, SOURCE_CLASS_V, "профиль"),
        "x": EstimateLine("x", "X", "%", 0.0, 0.0, {"rate": 0.22}, "B", ["work"], "т.87"),
    }
    assert fingerprint(lines_from_json(lines_to_json(lines))) == fingerprint(lines)


# --- адаптер: одноэтапный модуль (без stages()) = один этап ---


class LegacyToyModule:
    """Модуль по прежнему контракту BudgetModule, без stages()."""

    code = "legacy_toy"

    def input_schema(self):
        return [Field("n", "N", "number", True)]

    def field_options(self, field_name, refs):
        return []

    def build_volumes(self, data, refs):
        return {"work": data.get("n", 0)}

    def build_estimate(self, volumes, refs):
        return {
            "legacy_toy.work": EstimateLine(
                "legacy_toy.work", "Работа", "шт", volumes["work"], 3.0, {}, "A", [], "",
            )
        }

    def check(self, estimate):
        return ["проверка"]


def test_adapter_wraps_legacy_module_as_single_stage():
    module = LegacyToyModule()
    staged = as_staged(module)
    assert staged.is_single_stage
    assert staged.code == module.code
    assert staged.stages[0].fields == module.input_schema()

    direct = module.build_estimate(module.build_volumes({"n": 4}, refs=None), refs=None)
    session = StagedSession(staged)
    session.inputs["estimate"] = {"n": 4}
    draft = session.compute("estimate", refs=None)
    assert draft.effective == direct
    assert draft.warnings == ["проверка"]

    result = session.confirm("estimate", refs=None)
    assert dict(result.lines) == direct
    assert session.last_stage_status() == STATUS_CONFIRMED


# --- переведённые модули: этапы дают ту же смету, что прежний прямой вызов ---

# Те же входные данные, что в run_budget_stub.py (+ группа съёмки у
# геодезии, чтобы попали и групповые строки).
MODULE_CASES = {
    "geology": (GeologyModule, GEOLOGY_INPUT, {}),
    "geophysics": (GeophysicsModule, {"vez_count": 80, "mpv_count": 400}, {}),
    "geodesy": (
        GeodesyModule,
        {
            "oporny_points_count": 8,
            "privyazka_count": 50,
            "topo_surveys": [
                {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2.0},
                {"topo_scale": "1:2000", "topo_section": 1.0, "topo_area": 10.0},
            ],
            "topo_plans": [{"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2.0}],
        },
        {},
    ),
    "ecology": (
        EcologyModule,
        {
            "fund_materials_count": 3,
            "subbase_volume": 1,
            "recon_length_km": 12,
            "observation_points": 30,
            "radiation_area": 760,
            "radon_points": 200,
            "physical_factors_points": 10,
            "sampling": [
                {"sample_type": "soil_chemical", "sample_count": 100},
                {"sample_type": "surface_water_chemical", "sample_count": 3},
                {"sample_type": "soil_chemical", "sample_count": 60},
            ],
            "data_acquisition_count": 26,
        },
        {
            "subbase_item": "14",
            "passability": "satisfactory",
            "route_field_coefficient": 1.25,
            "route_kameral_coefficient": 1.1,
            "lab_total_base": 5000,
        },
    ),
}
# Строки сметы, у которых qty на этапе сметы ПРАВИТСЯ (своей строки
# ведомости нет) — у остальных строк kind A только цена (решение №5).
# Строки «доля от базы» (kind B/C: транспорт, техотчёт, камеральная
# обработка хим.состава воды) с Э2 (25.10.2026) правятся только ставкой.
QTY_EDITABLE_ON_ESTIMATE = {
    "geology": set(),
    "geophysics": set(),
    "geodesy": set(),
    # камеральная обработка лабораторных: её "объём" — денежный итог
    # лабораторных, параметр этапа сметы, строки ведомости нет
    "ecology": {"ecology.kameral.lab"},
}
def staged_estimate(module, volume_inputs, refs, estimate_inputs=None):
    session = StagedSession(as_staged(module))
    session.inputs["volumes"] = volume_inputs
    session.inputs["estimate"] = estimate_inputs or {}
    session.confirm("volumes", refs)
    result = session.confirm("estimate", refs)
    assert session.last_stage_status() == STATUS_CONFIRMED
    return session, dict(result.lines)


@pytest.mark.parametrize("code", list(MODULE_CASES))
def test_staged_module_gives_same_estimate_as_direct_call(code, refs):
    module_class, volume_input, estimate_input = MODULE_CASES[code]
    module = module_class()
    staged = as_staged(module)
    assert [s.key for s in staged.stages] == ["volumes", "estimate"]
    # поля этапов вместе — ровно прежняя форма ввода, без потерь и дублей
    assert sorted(f.name for s in staged.stages for f in s.fields) == sorted(
        f.name for f in module.input_schema()
    )

    data = {**volume_input, **estimate_input}
    direct = module.build_estimate(module.build_volumes(data, refs=refs), refs=refs)
    _session, lines = staged_estimate(module, volume_input, refs, estimate_input)
    assert lines == direct


@pytest.mark.parametrize("code", list(MODULE_CASES))
def test_volume_backed_estimate_lines_allow_only_price_edits(code, refs):
    module_class, volume_input, estimate_input = MODULE_CASES[code]
    session, lines = staged_estimate(module_class(), volume_input, refs, estimate_input)
    qty_editable = set()
    for line in lines.values():
        fields = session.editable_fields("estimate", line)
        if line.kind in ("B", "C"):
            assert fields == ("rate",), line.id
            continue
        assert "price" in fields, line.id
        if "qty" in fields:
            qty_editable.add(line.id)
    assert qty_editable == QTY_EDITABLE_ON_ESTIMATE[code]


# --- геофизика ---


def test_geophysics_volume_sheet():
    sheet = GeophysicsModule().build_volume_sheet({"vez_count": 80, "mpv_count": 400})
    assert [(k, line.qty, line.source) for k, line in sheet.items()] == [
        ("vez", 80, SOURCE_INPUT),
        ("mpv", 400, SOURCE_INPUT),
        ("kameralka_mpv", 400, SOURCE_CLASS_A),
    ]
    pinned = GeophysicsModule().build_volume_sheet(
        {"vez_count": 80, "mpv_count": 400}, pinned={"kameralka_mpv": {"qty": 350}}
    )
    assert (pinned["kameralka_mpv"].qty, pinned["kameralka_mpv"].source) == (350, SOURCE_OPERATOR)


def test_geophysics_coefficient_warnings_belong_to_estimate_stage(refs):
    session, lines = staged_estimate(GeophysicsModule(), {"vez_count": 80, "mpv_count": 400}, refs)
    assert session.compute("volumes", refs).warnings == []
    # + с 30.10.2026 предупреждение «начисления не применены» (ставки не
    # заданы в данных объекта) — тоже об этапе сметы (core/accruals.py)
    from core.accruals import accrual_warnings
    from modules.geophysics.module import ACCRUALS
    assert session.compute("estimate", refs).warnings == (
        GeophysicsModule().check(lines) + accrual_warnings(ACCRUALS, refs)
    )


# --- пилот: геология, ведомость → смета ---


def test_geology_is_two_stage():
    staged = as_staged(GeologyModule())
    assert [s.key for s in staged.stages] == ["volumes", "estimate"]
    # с Э6а (30.10.2026) коэффициенты к расценкам — поля этапа сметы
    from modules.geology.module import COEFFICIENT_FIELDS
    schema = GeologyModule().input_schema()
    assert staged.stages[0].fields == [f for f in schema if f.name not in COEFFICIENT_FIELDS]
    assert [f.name for f in staged.stages[1].fields] == list(COEFFICIENT_FIELDS)


def test_geology_volume_sheet_generates_classes_a_and_b():
    sheet = GeologyModule().build_volume_sheet(GEOLOGY_INPUT)
    assert sheet["drilling_catII"].qty == 172 and sheet["drilling_catII"].source == SOURCE_INPUT
    assert sheet["drilling_count"].qty == 30
    assert (sheet["geodesy_privyazka"].qty, sheet["geodesy_privyazka"].source) == (30, SOURCE_CLASS_A)
    assert (sheet["kameral_bureniya"].qty, sheet["kameral_bureniya"].source) == (774, SOURCE_CLASS_A)
    assert sheet["programma_rabot"].qty == 1
    assert sheet["tehotchet"].qty == 1
    assert (sheet["termometriya"].qty, sheet["termometriya"].source) == (15, SOURCE_CLASS_B)
    assert sheet["monolity_dispersnye"].source == SOURCE_CLASS_V
    assert sheet["lab_summarnaya_vlazhnost"].source == SOURCE_LAB
    assert all(line.name and line.unit for line in sheet.values())


def test_geology_build_volumes_still_matches_volume_sheet():
    module = GeologyModule()
    sheet = module.build_volume_sheet(GEOLOGY_INPUT)
    volumes = module.build_volumes(GEOLOGY_INPUT, refs=None)
    assert volumes == {k: line.qty for k, line in sheet.items() if k != "drilling_count"}


def test_geology_dependent_volume_follows_pinned_source_unless_overridden_itself():
    module = GeologyModule()
    sheet = module.build_volume_sheet(GEOLOGY_INPUT, pinned={"monolity_dispersnye": {"qty": 40}})
    assert (sheet["monolity_dispersnye"].qty, sheet["monolity_dispersnye"].source) == (40, SOURCE_OPERATOR)
    assert sheet["lab_summarnaya_vlazhnost"].qty == 40

    sheet = module.build_volume_sheet(
        GEOLOGY_INPUT,
        pinned={"monolity_dispersnye": {"qty": 40}, "lab_summarnaya_vlazhnost": {"qty": 35}},
    )
    assert sheet["lab_summarnaya_vlazhnost"].qty == 35
    assert sheet["lab_summarnaya_vlazhnost"].source == SOURCE_OPERATOR


def test_geology_volume_warnings_disappear_for_operator_values():
    module = GeologyModule()
    default_warnings = module.check_volume_sheet(module.build_volume_sheet(GEOLOGY_INPUT))
    pinned = {
        key: {"qty": 1}
        for key in ("monolity_dispersnye", "monolity_skalnye", "proby_vody")
    }
    own_warnings = module.check_volume_sheet(module.build_volume_sheet(GEOLOGY_INPUT, pinned))
    assert len(default_warnings) == 6  # 3 класса В + 3 лабораторных
    assert len(own_warnings) == 3  # остались только лабораторные


def test_geology_input_lines_are_not_editable_on_volume_stage():
    session = StagedSession(as_staged(GeologyModule()))
    session.inputs["volumes"] = GEOLOGY_INPUT
    session.overrides["volumes"].apply("drilling_count", "qty", 30, 99)
    draft = session.compute("volumes", refs=None)
    assert draft.effective["drilling_count"].qty == 30
    assert [e.line_id for e in draft.not_applied] == ["drilling_count"]


def test_geology_end_to_end_without_edits_matches_legacy_estimate(refs):
    module = GeologyModule()
    legacy = module.build_estimate(module.build_volumes(GEOLOGY_INPUT, refs=refs), refs=refs)

    session = StagedSession(as_staged(module))
    session.inputs["volumes"] = GEOLOGY_INPUT
    session.confirm("volumes", refs)
    assert session.compute("estimate", refs).effective == legacy


def test_geology_full_scenario_with_return_to_volume_sheet(refs):
    session = StagedSession(as_staged(GeologyModule()))
    session.inputs["volumes"] = dict(GEOLOGY_INPUT)

    # этап 1: оператор вводит фактические монолиты — лабораторные следуют
    session.overrides["volumes"].apply("monolity_dispersnye", "qty", 74.4, 60)
    volumes = session.confirm("volumes", refs)
    assert volumes.lines["lab_summarnaya_vlazhnost"].qty == 60

    # этап 2: объём строки с ведомостью не правится, цена — правится
    log = session.overrides["estimate"]
    log.apply("geology.monolity_dispersnye", "qty", 60, 70)
    log.apply("geology.programma_rabot", "price", 500, 550)
    draft = session.compute("estimate", refs)
    assert draft.effective["geology.monolity_dispersnye"].qty == 60
    assert draft.effective["geology.lab_summarnaya_vlazhnost"].qty == 60
    assert draft.effective["geology.programma_rabot"].price == 550
    assert [(e.line_id, e.field) for e in draft.not_applied] == [("geology.monolity_dispersnye", "qty")]
    # у строки без строки ведомости (транспорт) qty по-прежнему правится
    assert session.editable_fields("estimate", draft.effective["geology.transport"]) == ("rate",)
    session.confirm("estimate", refs)
    assert session.last_stage_status() == STATUS_CONFIRMED

    # возврат на ведомость → смета устарела, но цела
    session.reopen("volumes")
    assert session.status("estimate") == STATUS_STALE
    session.inputs["volumes"] = {**GEOLOGY_INPUT, "drilling_count": 40}
    session.confirm("volumes", refs)
    assert session.status("estimate") == STATUS_STALE
    assert session.result("estimate").lines["geology.geodesy_privyazka"].qty == 30

    # переподтверждение сметы — по новой ведомости, правка цены сохранилась
    session.confirm("estimate", refs)
    fresh = session.result("estimate")
    assert fresh.is_stale is False
    assert fresh.lines["geology.geodesy_privyazka"].qty == 40
    assert fresh.lines["geology.programma_rabot"].price == 550


# --- геодезия ---


def test_geodesy_volume_sheet_has_one_line_per_group_row():
    data = MODULE_CASES["geodesy"][1]
    sheet = GeodesyModule().build_volume_sheet(data)
    assert list(sheet) == [
        "oporny_points_count", "privyazka_count", "topo_survey.0", "topo_survey.1", "topo_plan.0",
    ]
    assert all(line.source == SOURCE_INPUT for line in sheet.values())
    assert sheet["topo_survey.1"].qty == 10.0
    assert sheet["topo_survey.1"].name == "Инженерно-топографическая съёмка М1:2000, сечение рельефа 1.0 м"


def test_geodesy_estimate_uses_scale_from_confirmed_sheet_not_later_input(refs):
    data = MODULE_CASES["geodesy"][1]
    session = StagedSession(as_staged(GeodesyModule()))
    session.inputs["volumes"] = data
    session.confirm("volumes", refs)
    # ввод после подтверждения не влияет на смету — она от снимка
    session.inputs["volumes"] = {**data, "topo_surveys": []}
    lines = session.compute("estimate", refs).effective
    assert "geodesy.topo_survey.1" in lines
    # 1:2000 — цена т.9 п.17 листа Карьера (до трека 2, 03.11.2026 — «цена не найдена»)
    assert lines["geodesy.topo_survey.1"].price == 804.0
    assert "geodesy.topo_survey.1" in lines["geodesy.transport"].base_ids


# --- экология ---

ECOLOGY_VOLUMES = MODULE_CASES["ecology"][1]
ECOLOGY_PARAMETERS = MODULE_CASES["ecology"][2]


def test_ecology_parameters_are_estimate_stage_fields():
    volumes_stage, estimate_stage = as_staged(EcologyModule()).stages
    assert [f.name for f in estimate_stage.fields] == [
        "subbase_item", "passability", "route_field_coefficient",
        "route_kameral_coefficient", "lab_total_base", "heavy_metals_sediments_elements",
    ]
    assert not set(ECOLOGY_PARAMETERS) & {f.name for f in volumes_stage.fields}


def test_ecology_volume_sheet_contains_kameral_pairs_and_no_parameters():
    sheet = EcologyModule().build_volume_sheet(ECOLOGY_VOLUMES)
    assert sheet["program"].qty == 1 and sheet["report"].qty == 1
    assert sheet["soil_recon_length_km"].qty == 12
    assert sheet["route_length_km"].source == SOURCE_CLASS_A
    assert sheet["sampling_soil_chemical"].qty == 160  # две строки одного вида
    for pair, field_key in [
        ("recon", "recon_length_km"), ("soil_recon", "soil_recon_length_km"),
        ("route", "route_length_km"), ("observation_points", "observation_points"),
        ("radiation", "radiation_area"), ("radon", "radon_points"),
    ]:
        kameral = sheet[f"kameral_{pair}"]
        assert (kameral.qty, kameral.source) == (sheet[field_key].qty, SOURCE_CLASS_A)
    # параметров расценки и камеральной обработки лабораторных в ведомости нет
    assert not {"subbase_item", "passability", "lab_total_base", "kameral_lab"} & set(sheet)
    assert "route_field_coefficient" not in sheet


def test_ecology_kameral_pair_follows_pinned_field_line_unless_overridden():
    module = EcologyModule()
    sheet = module.build_volume_sheet(ECOLOGY_VOLUMES, pinned={"route_length_km": {"qty": 8}})
    assert sheet["route_length_km"].qty == 8
    assert sheet["kameral_route"].qty == 8
    assert sheet["kameral_recon"].qty == 12

    sheet = module.build_volume_sheet(
        ECOLOGY_VOLUMES,
        pinned={"route_length_km": {"qty": 8}, "kameral_route": {"qty": 5}},
    )
    assert (sheet["kameral_route"].qty, sheet["kameral_route"].source) == (5, SOURCE_OPERATOR)


def test_ecology_pinned_kameral_volume_reaches_estimate(refs):
    session = StagedSession(as_staged(EcologyModule()))
    session.inputs["volumes"] = ECOLOGY_VOLUMES
    session.inputs["estimate"] = ECOLOGY_PARAMETERS
    session.overrides["volumes"].apply("kameral_radiation", "qty", 760, 500)
    session.confirm("volumes", refs)
    lines = session.compute("estimate", refs).effective
    assert lines["ecology.radiation"].qty == 760
    assert lines["ecology.kameral.radiation"].qty == 500


def test_ecology_parameters_change_price_not_confirmed_volumes(refs):
    session = StagedSession(as_staged(EcologyModule()))
    session.inputs["volumes"] = ECOLOGY_VOLUMES
    session.confirm("volumes", refs)
    good = session.compute("estimate", refs).effective
    session.inputs["estimate"] = {"passability": "satisfactory", "lab_total_base": 5000}
    satisfactory = session.compute("estimate", refs).effective

    assert good["ecology.recon"].qty == satisfactory["ecology.recon"].qty == 12
    assert good["ecology.recon"].basis != satisfactory["ecology.recon"].basis
    assert satisfactory["ecology.kameral.lab"].qty == 5000
    # смена параметра сметы не делает ведомость устаревшей
    assert session.status("volumes") == STATUS_CONFIRMED


# --- черновик поверх восстановленных версий (apply_draft) ---


def test_draft_on_unconfirmed_stage_restores_its_input_and_edits():
    session = StagedSession(as_staged(ToyModule()))
    session.apply_draft("volumes", {"n": 7}, [Edit("work", "qty", 7, 11, None)])
    assert session.current == "volumes"
    assert session.inputs["volumes"] == {"n": 7}
    assert session.compute("volumes", refs=None).effective["work"].qty == 11
    assert session.last_stage_status() == STATUS_DRAFT


def test_draft_on_later_stage_keeps_confirmed_earlier_stage():
    session = toy_session(n=10)
    volumes = session.confirm("volumes", refs=None)

    restored = StagedSession(as_staged(ToyModule()))
    restored.restore([volumes])
    restored.apply_draft("estimate", {"price": 9}, [])
    assert restored.status("volumes") == STATUS_CONFIRMED
    assert restored.current == "estimate"
    assert restored.compute("estimate", refs=None).effective["toy.work"].price == 9
    # черновик последнего этапа — не подтверждён, в бюджет не идёт
    assert restored.last_stage_status() == STATUS_DRAFT


def test_draft_differing_from_confirmed_stage_reopens_it_and_marks_later_stale():
    session = toy_session(n=10)
    volumes = session.confirm("volumes", refs=None)
    estimate = session.confirm("estimate", refs=None)

    same = StagedSession(as_staged(ToyModule()))
    same.restore([volumes, estimate])
    same.apply_draft("volumes", {"n": 10}, [])
    assert same.status("volumes") == STATUS_CONFIRMED
    assert same.status("estimate") == STATUS_CONFIRMED

    changed = StagedSession(as_staged(ToyModule()))
    changed.restore([volumes, estimate])
    changed.apply_draft("volumes", {"n": 12}, [])
    assert changed.current == "volumes"
    assert changed.status("volumes") == STATUS_DRAFT
    assert changed.status("estimate") == STATUS_STALE


def test_draft_of_stage_that_is_not_ready_falls_back_to_first_open_stage():
    session = StagedSession(as_staged(ToyModule()))
    session.apply_draft("estimate", {"price": 4}, [])
    assert session.current == "volumes"
    assert session.inputs["estimate"] == {"price": 4}


def test_numbers_fingerprint_ignores_only_basis_text():
    """Итоговое ревью прогона 30.10: новая версия программы уточнила текст
    основания, числа те же — смета не «устарела» для сводного бюджета."""
    import dataclasses

    from core.models import EstimateLine
    from core.stages import fingerprint, numbers_fingerprint

    line = EstimateLine(id="a", name="Бурение", unit="п.м.", qty=10, price=5.0, coefs={},
                        kind="A", base_ids=[], basis="т.1 п.1")
    renamed = dataclasses.replace(line, basis="т.1 п.1 (уточнено)")
    more = dataclasses.replace(line, qty=11)
    assert fingerprint({"a": line}) != fingerprint({"a": renamed})
    assert numbers_fingerprint({"a": line}) == numbers_fingerprint({"a": renamed})
    assert numbers_fingerprint({"a": line}) != numbers_fingerprint({"a": more})
