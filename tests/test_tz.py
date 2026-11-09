"""Параметры ТЗ и черновик ведомости из них (core/tz.py, шаг 1 → 2, 03.11.2026).

Ядро: параметры ТЗ → ввод этапа «Ведомость объёмов» модулей (только ввод — формулы
модулей не дублируются), слияние с уже введённым без молчаливой перезаписи,
обратная совместимость данных объекта. Интерфейс (AppTest): ввод на странице 1,
«Предложить ведомость» → заполненный ввод и источник на странице 2, одна главная
кнопка, повторное предложение не затирает ручной ввод."""

import pytest

from app import ui
from core.object_data import ObjectData
from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.stages import StagedSession, as_staged
from core.tz import (
    KIND_ARCHIVE,
    KIND_ESTIMATE,
    KIND_EXECUTOR,
    KIND_INPUT,
    KIND_KEY,
    KIND_LABELS,
    KIND_MANUAL,
    KIND_RULE,
    KIND_TZ,
    MODE_FILL_EMPTY,
    MODE_REPLACE,
    OBJECT_AREA,
    OBJECT_LINEAR,
    PROPOSE_KEY,
    Proposal,
    TzParams,
    is_empty_value,
    key_volume_fields,
    merge_proposal,
    propose_stage_input,
    provenance_rows,
)
from modules.geodesy.module import CONDITION_STRIP, GeodesyModule
from modules.geology.module import GeologyModule
from tests.test_streamlit_app import goto, open_app
from tests.test_ui_guide import isolated_smeta_db, primary_buttons  # noqa: F401

# Тестовый линейный объект «Т-01» (числа условные, не объекта заказчика).
ROAD = TzParams(
    object_kind=OBJECT_LINEAR, length_km=10.0, survey_scale="1:1000", survey_section_m=0.5,
    isso_count=5, permafrost=True, seismicity=8, sections=tuple(ui.MODULES),
    hole_spacing_m=250, hole_depth_m=10, strip_width_m=100,
)


# --- параметры ТЗ ----------------------------------------------------------------


def test_params_roundtrip_and_empty():
    assert TzParams().to_dict() == {}
    assert TzParams().is_empty()
    assert TzParams.from_dict(ROAD.to_dict()) == ROAD
    assert ROAD.to_dict()["sections"] == list(ui.MODULES)


def test_bad_values_are_skipped_not_raised():
    tz = TzParams.from_dict({
        "object_kind": "space", "length_km": "двенадцать", "area_ha": -3, "isso_count": "4,0",
        "survey_scale": "1:777", "permafrost": "yes", "sections": ["geology", "unknown"],
        "new_key_from_newer_program": 1,
    })
    assert tz == TzParams(isso_count=4, sections=("geology",))


def test_holes_from_executor_or_spacing():
    assert TzParams(holes_count=7).holes().value == 7
    assert TzParams(holes_count=7).holes().kind == KIND_EXECUTOR
    holes = ROAD.holes()  # 10 км / 250 м = 40
    assert (holes.value, holes.kind) == (40, KIND_EXECUTOR)  # шаг — решение исполнителя
    assert TzParams(object_kind=OBJECT_LINEAR, length_km=10.1, hole_spacing_m=250).holes().value == 41
    assert TzParams(object_kind=OBJECT_LINEAR, length_km=10).holes() is None  # шага нет
    assert ROAD.drilling_total().value == 400


def test_survey_area_linear_and_area_object():
    area = ROAD.survey_area()  # 10 км × 100 м = 100 га
    assert (area.value, area.kind) == (100, KIND_RULE)
    area = TzParams(object_kind=OBJECT_AREA, area_ha=22.5).survey_area()
    assert (area.value, area.kind) == (22.5, KIND_TZ)
    assert TzParams(object_kind=OBJECT_LINEAR, length_km=10).survey_area() is None


def test_warnings_name_missing_length():
    assert TzParams(object_kind=OBJECT_LINEAR).warnings()
    assert ROAD.warnings() == []


# --- данные объекта: обратная совместимость -------------------------------------


def test_old_object_dict_without_tz_opens():
    old = {"code": "Т-01", "distance_km": 12, "regional_field_rate": 0.5}
    data = ObjectData.from_dict(old)
    assert data.tz == {}
    assert data.code == "Т-01"
    assert ObjectData().is_empty()


def test_object_data_keeps_tz_and_cleans_it():
    data = ObjectData.from_dict({"tz": {**ROAD.to_dict(), "length_km": "abc"}})
    assert TzParams.from_dict(data.tz).length_km is None
    assert TzParams.from_dict(data.tz).hole_depth_m == 10
    restored = ObjectData.from_dict(ObjectData(tz=ROAD.to_dict()).to_dict())
    assert TzParams.from_dict(restored.tz) == ROAD
    assert ObjectData.from_dict({"tz": "битое"}).tz == {}


def test_object_info_describes_tz():
    info = ObjectData(code="Т-01", tz=ROAD.to_dict()).info()
    assert "Протяжённость трассы, км: 10" in info["Параметры ТЗ"]
    assert "Параметры ТЗ" not in ObjectData(code="Т-01").info()


# --- слияние с введённым --------------------------------------------------------


def test_fill_empty_keeps_manual_input():
    current = {"drilling_count": 33, "rekognoscirovka_km": 0}
    merged = merge_proposal(current, {"drilling_count": 40, "rekognoscirovka_km": 10}, MODE_FILL_EMPTY)
    assert merged.inputs == {"drilling_count": 33, "rekognoscirovka_km": 10}
    assert merged.kept == ["drilling_count"] and merged.filled == ["rekognoscirovka_km"]
    assert merged.replaced == []


def test_replace_mode_lists_replaced():
    merged = merge_proposal({"drilling_count": 33, "other": 5}, {"drilling_count": 40}, MODE_REPLACE)
    assert merged.inputs == {"drilling_count": 40, "other": 5}
    assert merged.replaced == ["drilling_count"]


def test_default_group_row_counts_as_empty():
    # так форма рисует нетронутую строку группы: вариант по умолчанию, числа 0
    assert is_empty_value([{"topo_scale": "1:500", "topo_section": 0.0, "topo_area": 0.0}])
    assert not is_empty_value([{"topo_scale": "1:500", "topo_area": 2.0}])
    merged = merge_proposal(
        {"topo_surveys": [{"topo_scale": "1:500", "topo_section": 0.0, "topo_area": 0.0}]},
        {"topo_surveys": [{"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 100}]},
    )
    assert merged.filled == ["topo_surveys"]


def test_same_value_ignores_form_defaults_in_group_rows():
    proposed = {"drilling_by_category": [{"soil_category": None, "drilling_length": 400}]}
    current = {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 400.0}]}
    assert merge_proposal(current, proposed).kept == []


# --- предложение модулей -------------------------------------------------------


@pytest.mark.parametrize("code", list(ui.MODULES))
def test_proposal_uses_only_volume_stage_fields(code):
    module = ui.MODULES[code]["class"]()
    proposal = propose_stage_input(module, ROAD)
    assert isinstance(proposal, Proposal)
    names = {f.name for f in as_staged(module).stages[0].fields}
    assert set(proposal.inputs) <= names
    assert set(proposal.sources) <= names
    assert all(kind in KIND_LABELS for kind, _note in proposal.sources.values())


def test_module_without_method_gives_none():
    assert propose_stage_input(object(), ROAD) is None


def test_geology_fills_input_module_computes_classes():
    proposal = GeologyModule().propose_volumes(ROAD)
    assert proposal.inputs["drilling_count"] == 40
    assert proposal.inputs["drilling_by_category"][0]["drilling_length"] == 400
    assert proposal.sources["rekognoscirovka_km"][0] == KIND_ESTIMATE
    data = dict(proposal.inputs)
    data["drilling_by_category"] = [{"soil_category": "IV", "drilling_length": 400}]
    lines = GeologyModule().build_volume_sheet(data)
    assert lines["geodesy_privyazka"].qty == 40           # класс А: = скважин
    assert lines["kameral_bureniya"].qty == 400           # класс А: = метраж
    assert lines["termometriya"].qty == 20                # класс Б: 0,5 × скважин


def test_geology_without_executor_decisions_leaves_input():
    proposal = GeologyModule().propose_volumes(TzParams(object_kind=OBJECT_LINEAR, length_km=10))
    assert "drilling_count" not in proposal.inputs
    assert proposal.sources["drilling_count"][0] == KIND_INPUT


def test_geology_without_permafrost_warns_about_thermometry():
    notes = GeologyModule().propose_volumes(TzParams(permafrost=False)).notes
    assert any("Термометрия" in n for n in notes)


def test_geodesy_strip_survey_and_plan():
    proposal = GeodesyModule().propose_volumes(ROAD)
    survey = proposal.inputs["topo_surveys"][0]
    assert survey == {"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 100,
                      "survey_condition": CONDITION_STRIP}
    assert proposal.inputs["topo_plans"][0]["topo_area"] == 100
    assert proposal.inputs["trassa_km"] == 10
    assert proposal.sources["trassa_km"][0] == KIND_TZ
    assert proposal.sources["privyazka_count"][0] == KIND_ESTIMATE


def test_provenance_marks_manual_change():
    fields = as_staged(GeologyModule()).stages[0].fields
    proposal = GeologyModule().propose_volumes(ROAD)
    record = {"inputs": proposal.inputs, "sources": proposal.sources}
    rows = {r["Поле ввода"]: r for r in provenance_rows(fields, record, {"drilling_count": 55})}
    assert rows["Число скважин"]["Источник"] == KIND_LABELS[KIND_MANUAL]
    rows = {r["Поле ввода"]: r for r in provenance_rows(fields, record, proposal.inputs)}
    assert rows["Число скважин"]["Источник"] == KIND_LABELS[KIND_EXECUTOR]


@pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite")
def test_proposal_is_only_a_draft_volume_stage_computes():
    """Предложенный ввод геодезии считается этапом ведомости, ничего не подтверждено."""
    session = StagedSession(as_staged(GeodesyModule()))
    proposal = GeodesyModule().propose_volumes(ROAD)
    session.inputs["volumes"] = {**proposal.inputs, "oporny_points_count": 4}
    draft = session.compute("volumes", ReferenceBook())
    assert draft.effective["topo_survey.0"].qty == 100
    assert session.result("volumes") is None


# --- интерфейс (AppTest) ---------------------------------------------------------


def _fill_tz(at, sections=("geology", "geodesy")):
    at.selectbox(key="obj_tz_object_kind").set_value(OBJECT_LINEAR)
    at.number_input(key="obj_tz_length_km").set_value(10.0)
    at.selectbox(key="obj_tz_survey_scale").set_value("1:1000")
    at.number_input(key="obj_tz_survey_section_m").set_value(0.5)
    at.number_input(key="obj_tz_hole_spacing_m").set_value(250.0)
    at.number_input(key="obj_tz_hole_depth_m").set_value(10.0)
    at.number_input(key="obj_tz_strip_width_m").set_value(100.0)
    for code in sections:
        at.checkbox(key=f"obj_tz_sec_{code}").check()
    at.run(timeout=30)


def _propose(at):
    at.button(key="tz_propose").click()
    at.run(timeout=30)
    goto(at, "vedomost")


def test_propose_fills_volume_input_and_shows_source(isolated_smeta_db):  # noqa: F811
    at = open_app("obekt")
    assert at.button(key="tz_propose").disabled  # разделы не отмечены
    _fill_tz(at)
    assert at.exception == []
    assert primary_buttons(at) == [("Перейти к ведомости объёмов", True)]
    _propose(at)
    assert at.exception == []
    assert at.radio(key="module_choice").value == "geology"
    assert at.number_input(key="field_geology_drilling_count").value == 40
    assert at.number_input(key="group_geology_drilling_by_category_0_drilling_length").value == 400
    texts = " ".join(str(df.value.to_dict()) for df in at.dataframe)
    assert KIND_LABELS[KIND_RULE] in texts and KIND_LABELS[KIND_ESTIMATE] in texts
    # ничего не подтверждено, главная кнопка — одна; категорию грунта программа не выбирает
    # за оператора (ревью 03.11, №1): пока не выбрана — подтвердить нельзя, причина видна
    category = at.selectbox(key="group_geology_drilling_by_category_0_soil_category")
    assert category.value is None
    assert primary_buttons(at) == [("Подтвердить ведомость", False)]
    assert any("не выбрано: Категория" in c for c in [c.value for c in at.caption])
    category.set_value("IV")
    at.run(timeout=30)
    assert primary_buttons(at) == [("Подтвердить ведомость", True)]
    assert ui.STATUS_CONFIRMED not in [at.session_state["staged_sessions"]["geology"].status("volumes")]
    # второй выбранный раздел тоже заполнен
    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)
    assert at.number_input(key="group_geodesy_topo_surveys_0_topo_area").value == 100
    assert at.number_input(key="field_geodesy_trassa_km").value == 10


def test_repeat_proposal_keeps_manual_input(isolated_smeta_db):  # noqa: F811
    at = open_app("obekt")
    _fill_tz(at, sections=("geology",))
    _propose(at)
    at.number_input(key="field_geology_drilling_count").set_value(55)
    at.run(timeout=30)
    texts = " ".join(str(df.value.to_dict()) for df in at.dataframe)
    assert KIND_LABELS[KIND_MANUAL] in texts
    goto(at, "obekt")
    _propose(at)  # по умолчанию — только пустые поля
    assert at.number_input(key="field_geology_drilling_count").value == 55
    assert any("оставлен ваш ввод" in w.value and "Число скважин" in w.value for w in at.warning)
    # замена — только явным выбором, и с перечнем заменённого
    goto(at, "obekt")
    at.radio(key="tz_merge_mode").set_value(MODE_REPLACE)
    at.run(timeout=30)
    _propose(at)
    assert at.number_input(key="field_geology_drilling_count").value == 40
    assert any("заменён ввод" in w.value for w in at.warning)


def test_tz_params_saved_with_object_data(isolated_smeta_db):  # noqa: F811
    at = open_app("obekt")
    _fill_tz(at, sections=("geology",))
    tz = TzParams.from_dict(at.session_state["_run_context"].object_data.tz)
    assert tz.length_km == 10 and tz.sections == ("geology",)


def test_holes_count_has_no_float_artifact():
    """Ревью 03.11, №2: 8,05 км / 50 м = 161 скважина, а не 162."""
    assert TzParams(object_kind=OBJECT_LINEAR, length_km=8.05, hole_spacing_m=50).holes().value == 161


# --- режим «ключевые объёмы» (03.11.2026, прогон «два дня») ------------------------
# Числа условные (тестовый объект «Т-01»), не объекта заказчика.

KEYS = {
    "geology": {"holes_count": 20, "drilling_total_m": 240, "rekognoscirovka_km": 12,
                "gidronablyudeniya_m": 30},
    "geophysics": {"vez_points": 40, "mpv_observations": 200, "mpv_waves": "PS"},
    "geodesy": {"survey_area_ha": 80, "trassa_km": 9.5, "oporny_points": 6, "privyazka_points": 25},
    "ecology": {"recon_km": 9.5, "observation_points": 7, "radiation_ha": 50,
                "surface_water_samples": 4, "radionuclide_samples": 12},
    "hydromet": {"watercourses": 3, "basins": 5, "sounding_stvors": 30, "flow_measurements": 6,
                 "recon_route_km": 10},
}
ROAD_KEYS = TzParams.from_dict({**ROAD.to_dict(), "key_volumes": KEYS})


def key_proposal(code, tz=ROAD_KEYS):
    return propose_stage_input(ui.MODULES[code]["class"](), tz, PROPOSE_KEY)


def kind(proposal, name):
    return proposal.sources[name][0]


def test_key_volumes_roundtrip_and_cleaning():
    assert TzParams.from_dict(ROAD_KEYS.to_dict()) == ROAD_KEYS
    tz = TzParams.from_dict({"key_volumes": {
        "geology": {"holes_count": "12", "drilling_total_m": 0, "x": -5, "y": "  "},
        "unknown": {"a": 1}, "geodesy": "битое", "ecology": {}}})
    assert tz.key_volumes == {"geology": {"holes_count": 12.0}}
    # код варианта (выпадающий список) сохраняется строкой; TzParams хешируется
    assert TzParams.from_dict({"key_volumes": {"geophysics": {"mpv_waves": "PS"}}}).key_volumes ==         {"geophysics": {"mpv_waves": "PS"}}
    assert hash(ROAD_KEYS) == hash(TzParams.from_dict(ROAD_KEYS.to_dict()))
    assert TzParams.from_dict({"object_kind": OBJECT_LINEAR}).key_volumes == {}
    assert "Ключевые объёмы" in ROAD_KEYS.describe()
    # данные объекта с ключевыми объёмами открываются и сохраняют их
    data = ObjectData.from_dict({"tz": ROAD_KEYS.to_dict()})
    assert TzParams.from_dict(data.tz).key_volumes["hydromet"]["basins"] == 5


@pytest.mark.parametrize("code", list(ui.MODULES))
def test_key_proposal_fields_kinds_and_fallback(code):
    module = ui.MODULES[code]["class"]()
    fields = key_volume_fields(module)
    assert 3 <= len(fields) <= 5
    assert {f.name for f in fields} == set(KEYS[code])
    proposal = key_proposal(code)
    names = {f.name for f in as_staged(module).stages[0].fields}
    assert set(proposal.inputs) <= names and set(proposal.sources) <= names
    assert all(k in KIND_LABELS for k, _ in proposal.sources.values())
    assert any(k == KIND_KEY for k, _ in proposal.sources.values())
    # пояснения: «жёсткая связь: …» / «оценка по архиву: … — проверьте»
    for k, note in proposal.sources.values():
        if k == KIND_ARCHIVE:
            assert note.startswith("оценка по архиву:") and note.endswith("проверьте")
    # без ключевых объёмов — то же, что предложение по ТЗ
    assert propose_stage_input(module, ROAD, PROPOSE_KEY).inputs == module.propose_volumes(ROAD).inputs
    # модуль без режима — как без метода
    assert propose_stage_input(object(), ROAD_KEYS, PROPOSE_KEY) is None


def test_key_geology_links_and_module_classes():
    proposal = key_proposal("geology")
    assert proposal.inputs["drilling_count"] == 20 and kind(proposal, "drilling_count") == KIND_KEY
    assert proposal.inputs["kreplenie_length"] == 30 and kind(proposal, "kreplenie_length") == KIND_RULE
    assert proposal.sources["kreplenie_length"][1].startswith("жёсткая связь:")
    assert proposal.inputs["lab_korroziya_beton_count"] == proposal.inputs["lab_korroziya_stal_count"]
    assert kind(proposal, "lab_korroziya_stal_count") == KIND_ARCHIVE
    # производное от оценки — тоже оценка (ревью №2)
    assert kind(proposal, "lab_korroziya_beton_count") == KIND_ARCHIVE
    assert kind(proposal, "lab_glinistye_count") == KIND_ARCHIVE
    # глинистые + песчаные = монолиты дисперсных (типовой профиль модуля)
    data = dict(proposal.inputs)
    data["drilling_by_category"] = [{"soil_category": "IV", "drilling_length": 240}]
    lines = GeologyModule().build_volume_sheet(data)
    assert (proposal.inputs["lab_glinistye_count"] + proposal.inputs["lab_peschanye_count"]
            == round(lines["monolity_dispersnye"].qty))
    # модуль сам: камеральная бурения = метраж без гидронаблюдений, с ними — отдельно
    assert lines["kameral_bureniya"].qty == 210 and lines["kameral_bureniya_gidro"].qty == 30
    assert lines["geodesy_privyazka"].qty == 20 and lines["kameral_rekognoscirovki"].qty == 12


def test_key_geophysics():
    from modules.geophysics.module import WAVES_BOTH, YES, GeophysicsModule

    proposal = key_proposal("geophysics")
    assert proposal.inputs["mpv_waves"] == WAVES_BOTH
    # лист геофизики — у одного объекта: отчёт — оценка, не «жёсткая связь» (ревью №3)
    assert proposal.inputs["report"] == YES and kind(proposal, "report") == KIND_ARCHIVE
    assert kind(proposal, "office_vez") == KIND_ARCHIVE
    lines = GeophysicsModule().build_volume_sheet(proposal.inputs)
    assert lines["kameralka_mpv"].qty == 200 and lines["kameralka_mpv_s"].qty == 200
    # тип волн — выпадающий список; продольные = значение формы по умолчанию
    from core.tz import key_volume_options
    from modules.geophysics.module import DEFAULT_CHOICE, WAVES_OPTIONS
    assert [v for v, _ in key_volume_options(GeophysicsModule(), "mpv_waves")] == ["P", "S", "PS"]
    p = key_proposal("geophysics", TzParams.from_dict(
        {"key_volumes": {"geophysics": {"mpv_observations": 10, "mpv_waves": "P"}}}))
    assert p.inputs["mpv_waves"] == DEFAULT_CHOICE and DEFAULT_CHOICE in dict(WAVES_OPTIONS)
    # тип волн без объёма МПВ — предупреждение, а не молчание (ревью №4)
    p = key_proposal("geophysics", TzParams.from_dict(
        {"key_volumes": {"geophysics": {"vez_points": 5, "mpv_waves": "S"}}}))
    assert "mpv_waves" not in p.inputs and any("тип волн МПВ задан" in n for n in p.notes)


def test_key_geodesy():
    proposal = key_proposal("geodesy")
    for name in ("height_points_count", "oporny_kameral_points_count", "znaki_count"):
        assert proposal.inputs[name] == 6 and kind(proposal, name) == KIND_RULE
    survey, plan = proposal.inputs["topo_surveys"][0], proposal.inputs["topo_plans"][0]
    assert survey["topo_area"] == plan["topo_area"] == 80 and survey["topo_scale"] == "1:1000"
    assert kind(proposal, "topo_plans") == KIND_RULE
    assert proposal.inputs["privyazka_count"] == 25  # своё число, не скважины геологии
    assert kind(proposal, "base_months") == KIND_ARCHIVE
    # без масштаба в ТЗ строка съёмки не предлагается, причина — в заметках
    no_scale = TzParams.from_dict({"key_volumes": {"geodesy": {"survey_area_ha": 10}}})
    p = key_proposal("geodesy", no_scale)
    assert "topo_surveys" not in p.inputs and any("масштаба" in n for n in p.notes)


def test_key_ecology_lab_from_samples():
    from modules.ecology.module import EcologyModule

    proposal = key_proposal("ecology")
    samples = {r["sample_type"]: r["sample_count"] for r in proposal.inputs["sampling"]}
    analyses = {r["analysis_type"]: r["analysis_count"] for r in proposal.inputs["lab_analyses"]}
    assert samples == {"surface_water_chemical": 4, "bottom_sediments_chemical": 4,
                       "soil_radionuclides": 12}
    assert analyses["surface_water"] == 4 and analyses["oil_sediments"] == 4
    assert analyses["gamma_spectrometry"] == 12 and analyses["heavy_metals_soil"] == 12
    assert proposal.inputs["radiation_area"] == 50  # ключевой объём перекрывает оценку по ТЗ
    lines = EcologyModule().build_volume_sheet(proposal.inputs)
    assert lines["soil_recon_length_km"].qty == 9.5 and lines["kameral_observation_points"].qty == 7


def test_key_hydromet_basins_and_soundings():
    from modules.hydromet.module import HydrometModule

    proposal = key_proposal("hydromet")
    i = proposal.inputs
    assert i["depth_soundings"] == 30 and kind(proposal, "depth_soundings") == KIND_RULE
    assert i["basin_slope_count"] == 5 and i["max_flow_first"] == 1 and i["min_flow_next"] == 4
    assert i["flow_graphs_count"] == i["rating_curve_count"] == 6
    assert i["recon_river_km"] == 3 and i["photo_count"] == 30
    lines = HydrometModule().build_volume_sheet(i)
    assert lines["kameral_flow"].qty == 6
    # один водосбор — «последующих» нет; без измерений расхода — 2 на водоток (оценка)
    one = TzParams.from_dict({"key_volumes": {"hydromet": {"basins": 1, "watercourses": 2}}})
    p = key_proposal("hydromet", one)
    assert "max_flow_next" not in p.inputs and p.inputs["flow_measurements"] == 4
    # графики и кривая от оценённых измерений — оценка, от ключевых — жёсткая связь (ревью №2)
    assert kind(p, "flow_graphs_count") == KIND_ARCHIVE == kind(p, "rating_curve_count")
    assert kind(proposal, "flow_graphs_count") == KIND_RULE


def _fill_keys(at):
    at.radio(key="tz_propose_mode").set_value(PROPOSE_KEY)
    at.run(timeout=30)
    for name, value in KEYS["geology"].items():
        at.number_input(key=f"obj_tz_kv_geology_{name}").set_value(value)
    at.run(timeout=30)


def test_ui_key_mode_fills_input_and_shows_origin(isolated_smeta_db):  # noqa: F811
    at = open_app("obekt")
    _fill_tz(at, sections=("geology",))
    assert not [n for n in at.number_input if (n.key or "").startswith("obj_tz_kv_")]  # режим ТЗ
    _fill_keys(at)
    assert at.exception == []
    assert primary_buttons(at) == [("Перейти к ведомости объёмов", True)]
    tz = TzParams.from_dict(at.session_state["_run_context"].object_data.tz)
    assert tz.key_volumes["geology"]["holes_count"] == 20  # хранится с данными объекта
    _propose(at)
    assert at.exception == []
    assert at.number_input(key="field_geology_drilling_count").value == 20  # ключевой, а не 40 из шага
    assert at.number_input(key="field_geology_kreplenie_length").value == 30
    texts = " ".join(str(df.value.to_dict()) for df in at.dataframe)
    assert KIND_LABELS[KIND_KEY] in texts and KIND_LABELS[KIND_ARCHIVE] in texts
    assert "жёсткая связь:" in texts

    # повтор не затирает ручной ввод
    at.number_input(key="field_geology_kreplenie_length").set_value(99)
    at.run(timeout=30)
    goto(at, "obekt")
    _propose(at)
    assert at.number_input(key="field_geology_kreplenie_length").value == 99
    assert any("оставлен ваш ввод" in w.value for w in at.warning)


def test_merge_updates_previous_program_proposal_but_keeps_manual():
    """Ревью №5: значение, оставшееся от прошлого предложения программы, — не ввод
    оператора: новое предложение (по ключевым объёмам) его заменяет и в режиме
    «только пустые»; изменённое вручную — остаётся."""
    previous = {"drilling_count": 40, "rekognoscirovka_km": 10.0}
    current = {"drilling_count": 40, "rekognoscirovka_km": 7.0}  # км правил вручную
    merged = merge_proposal(current, {"drilling_count": 20, "rekognoscirovka_km": 12.0},
                            MODE_FILL_EMPTY, previous)
    assert merged.inputs == {"drilling_count": 20, "rekognoscirovka_km": 7.0}
    assert merged.updated == ["drilling_count"] and merged.kept == ["rekognoscirovka_km"]
    # без прошлого предложения — как раньше
    assert merge_proposal(current, {"drilling_count": 20}).kept == ["drilling_count"]


def test_ui_tz_then_key_mode_replaces_program_values(isolated_smeta_db):  # noqa: F811
    at = open_app("obekt")
    _fill_tz(at, sections=("geology",))
    _propose(at)  # по ТЗ: скважин 40 (шаг 250 м на 10 км)
    assert at.number_input(key="field_geology_drilling_count").value == 40
    goto(at, "obekt")
    _fill_keys(at)
    _propose(at)  # по ключевым объёмам, режим «только пустые»
    assert at.exception == []
    assert at.number_input(key="field_geology_drilling_count").value == 20
    assert any("обновлено прошлое предложение программы" in w.value and "Число скважин" in w.value
               for w in at.warning)
    assert not any("оставлен ваш ввод" in w.value and "Число скважин" in w.value for w in at.warning)


def test_ui_waves_key_is_a_selectbox(isolated_smeta_db):  # noqa: F811
    at = open_app("obekt")
    _fill_tz(at, sections=("geophysics",))
    at.radio(key="tz_propose_mode").set_value(PROPOSE_KEY)
    at.run(timeout=30)
    box = at.selectbox(key="obj_tz_kv_geophysics_mpv_waves")
    assert box.value is None
    box.set_value("PS")
    at.number_input(key="obj_tz_kv_geophysics_mpv_observations").set_value(100)
    at.run(timeout=30)
    assert at.exception == []
    tz = TzParams.from_dict(at.session_state["_run_context"].object_data.tz)
    assert tz.key_volumes["geophysics"] == {"mpv_waves": "PS", "mpv_observations": 100}
