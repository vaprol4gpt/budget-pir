"""AppTest-проверка app/streamlit_app.py — выполняет реальный скрипт
интерфейса без сети/браузера (streamlit.testing.v1.AppTest). Раньше
такие проверки делались только разовыми scratch-скриптами (см.
записи разработки, записи разработки) и не
коммитились как pytest-тесты — это первый коммит с постоянным тестом
такого рода, а не "расширение существующего", хотя по сути он и
проверяет ту же идею (переключение между модулями не теряет
состояние), только теперь на ТРЁХ модулях и как настоящий тест."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.persistence as persistence_module
from app.ui import money_text, rub
from core.persistence import SmetaStore
from core.references import ReferenceBook
from core.stages import STATUS_CONFIRMED, STATUS_DRAFT, STATUS_STALE

APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")

# Страницы меню (с 29.10.2026 интерфейс разнесён по шагам процесса компании,
# app/ui.py::PAGES). Путь — относительно главного скрипта.
PAGE_FILES = {
    "obekt": "app_pages/p1_obekt.py",
    "vedomost": "app_pages/p2_vedomost.py",
    "smeta": "app_pages/p3_smeta.py",
    "minimum": "app_pages/p4_minimum.py",
    "svodny": "app_pages/p5_svodny.py",
    "spravochniki": "app_pages/p6_spravochniki.py",
    "versii": "app_pages/p7_versii.py",
}


def goto(at, page):
    """Перейти на страницу меню и перерисовать."""
    at.switch_page(PAGE_FILES[page])
    at.run(timeout=30)
    return at


def open_app(page="vedomost"):
    """Запуск интерфейса и переход на страницу. По умолчанию — «Ведомость
    объёмов»: до разнесения по меню форма модуля была на единственном
    экране сразу; большинство тестов начинают с неё. Подтверждение
    ведомости само переводит на страницу сметы, возврат к ведомости — на
    страницу ведомости (app/ui.py::go_to_stage)."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    if page != "obekt":
        goto(at, page)
    return at


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    # SmetaStore() без явного db_path резолвит core.persistence.DEFAULT_DB_PATH
    # ВНУТРИ __init__ на каждый вызов (см. core/persistence.py) — поэтому
    # monkeypatch этой константы ДО прогона AppTest корректно изолирует
    # тест от реальной data/smety.sqlite.
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


# --- целочисленный ввод: реальный отрисованный виджет, не изолированная функция ---
#
# Регрессия на реальную находку: core/formatting.py (число знаков в
# УЖЕ ПОСЧИТАННОЙ строке сметы) никак не влияет на ВХОДНЫЕ number_input
# — Streamlit сам выбирает step=0.01/format="%0.2f" для float-полей
# без явного step=, поэтому "Число скважин" показывалось как "30,00"
# несмотря на зелёные тесты предыдущей правки (они проверяли только
# результат расчёта, не сами поля ввода). Эти тесты читают атрибуты
# РЕАЛЬНО отрисованного виджета (.step/.value), а не Field.integer
# саму по себе.


def test_drilling_count_input_renders_as_integer_widget(isolated_smeta_db):
    at = open_app()

    widget = at.number_input(key="field_geology_drilling_count")
    assert widget.step == 1
    assert isinstance(widget.value, int)


def test_geodesy_count_inputs_render_as_integer_widgets(isolated_smeta_db):
    at = open_app()

    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)

    for key in ("field_geodesy_oporny_points_count", "field_geodesy_privyazka_count"):
        widget = at.number_input(key=key)
        assert widget.step == 1, f"{key}: step={widget.step!r}, ожидался 1 (целое число)"
        assert isinstance(widget.value, int), f"{key}: value={widget.value!r} не int"


def test_geophysics_count_inputs_render_as_integer_widgets(isolated_smeta_db):
    at = open_app()

    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)

    for key in ("field_geophysics_vez_count", "field_geophysics_mpv_count"):
        widget = at.number_input(key=key)
        assert widget.step == 1, f"{key}: step={widget.step!r}, ожидался 1 (целое число)"


def test_drilling_length_input_still_renders_as_decimal_widget(isolated_smeta_db):
    # Регрессия в обратную сторону: метраж бурения (не integer=True)
    # должен остаться дробным полем, не задет правкой выше.
    at = open_app()

    widget = at.number_input(key="group_geology_drilling_by_category_0_drilling_length")
    assert widget.step == 0.01


def test_drilling_count_entered_value_survives_as_whole_number(isolated_smeta_db):
    at = open_app()

    at.number_input(key="field_geology_drilling_count").set_value(30)
    at.run(timeout=30)

    widget = at.number_input(key="field_geology_drilling_count")
    assert widget.value == 30
    assert widget.step == 1


def test_module_radio_offers_all_five_modules(isolated_smeta_db):
    at = open_app()

    assert at.exception == []
    assert at.radio(key="module_choice").options == [
        "Геология (ОИГ)",
        "Геофизика (ИГФИ)",
        "Геодезия (ИГДИ)",
        "Экология (ИЭИ)",
        "Гидрометеорология (ИГМИ)",
    ]


def test_hydromet_two_stages_through_interface(isolated_smeta_db):
    """ИГМИ в интерфейсе: объёмы на этапе ведомости, параметры расценки —
    только на этапе сметы, у ставок нет значения по умолчанию (первый
    вариант списка — пустой), подтверждение сметы проходит."""
    at = open_app()
    at.radio(key="module_choice").set_value("hydromet")
    at.run(timeout=30)
    assert at.exception == []

    stage_one_keys = {w.key for w in at.number_input} | {w.key for w in at.selectbox}
    stage_one_keys.discard(None)
    assert "field_hydromet_recon_route_km" in stage_one_keys
    assert not any("internal_transport_rate" in key for key in stage_one_keys)

    at.number_input(key="field_hydromet_recon_route_km").set_value(3.0)
    at.number_input(key="field_hydromet_flow_measurements").set_value(2)
    at.run(timeout=30)
    at.button(key="confirm_hydromet_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.exception == []

    assert at.selectbox(key="field_hydromet_estimate_internal_transport_rate").value == ""
    assert at.selectbox(key="field_hydromet_estimate_complexity_category").value == ""
    at.selectbox(key="field_hydromet_estimate_complexity_category").set_value("III")
    at.selectbox(key="field_hydromet_estimate_internal_transport_rate").set_value("0.1875")
    at.selectbox(key="field_hydromet_estimate_river_width").set_value("до 20 м")
    at.number_input(key="field_hydromet_estimate_field_regional_coefficient").set_value(1.85)
    at.number_input(key="field_hydromet_estimate_external_transport_rate").set_value(0.364)
    at.number_input(key="field_hydromet_estimate_distance_km").set_value(10.0)
    at.number_input(key="field_hydromet_estimate_program_price").set_value(800.0)
    at.run(timeout=30)
    at.button(key="confirm_hydromet_estimate").click()
    at.run(timeout=30)

    assert at.exception == []
    session = at.session_state["staged_sessions"]["hydromet"]
    assert session.last_stage_status() == STATUS_CONFIRMED
    lines = session.result("estimate").lines
    assert lines["hydromet.kameral.recon_route"].qty == 3
    assert "не начисляется" in lines["hydromet.org_liquidation"].name  # 10 км ≤ 25


def test_switching_between_three_modules_preserves_each_module_state(isolated_smeta_db):
    at = open_app()

    # геология — категория/метраж бурения теперь group-поле
    # (drilling_by_category), см. core/module_base.py::Field.group_fields
    at.number_input(key="group_geology_drilling_by_category_0_drilling_length").set_value(450)
    at.number_input(key="field_geology_drilling_count").set_value(30)
    at.selectbox(key="group_geology_drilling_by_category_0_soil_category").set_value("IV")
    at.run(timeout=30)

    # геофизика
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    at.number_input(key="field_geophysics_vez_count").set_value(80)
    at.number_input(key="field_geophysics_mpv_count").set_value(400)
    at.run(timeout=30)

    # геодезия
    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)
    at.number_input(key="field_geodesy_oporny_points_count").set_value(8)
    at.number_input(key="field_geodesy_privyazka_count").set_value(50)
    at.run(timeout=30)

    assert at.exception == []

    # назад на геологию — ввод должен быть на месте
    at.radio(key="module_choice").set_value("geology")
    at.run(timeout=30)
    assert at.number_input(key="group_geology_drilling_by_category_0_drilling_length").value == 450.0
    assert at.number_input(key="field_geology_drilling_count").value == 30.0
    assert at.selectbox(key="group_geology_drilling_by_category_0_soil_category").value == "IV"

    # назад на геофизику
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    assert at.number_input(key="field_geophysics_vez_count").value == 80.0
    assert at.number_input(key="field_geophysics_mpv_count").value == 400.0

    # назад на геодезию
    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)
    assert at.number_input(key="field_geodesy_oporny_points_count").value == 8.0
    assert at.number_input(key="field_geodesy_privyazka_count").value == 50.0

    assert at.exception == []


def test_geology_add_row_button_creates_second_drilling_category(isolated_smeta_db):
    at = open_app()

    at.number_input(key="group_geology_drilling_by_category_0_drilling_length").set_value(172)
    at.selectbox(key="group_geology_drilling_by_category_0_soil_category").set_value("II")
    at.run(timeout=30)

    at.button(key="add_geology_drilling_by_category").click()
    at.run(timeout=30)

    # вторая строка группы появилась — свой независимый набор виджетов
    at.number_input(key="group_geology_drilling_by_category_1_drilling_length").set_value(602)
    at.selectbox(key="group_geology_drilling_by_category_1_soil_category").set_value("IV")
    at.run(timeout=30)

    at.number_input(key="field_geology_drilling_count").set_value(30)
    at.run(timeout=30)
    # Геология — двухэтапная (core/stages.py): вместо "Рассчитать" —
    # подтверждение ведомости, смета строится на втором этапе.
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")

    assert at.exception == []
    session = at.session_state["staged_sessions"]["geology"]
    volumes = session.result("volumes").lines
    assert volumes["drilling_catII"].qty == 172
    assert volumes["drilling_catIV"].qty == 602
    rows = session.compute("estimate", ReferenceBook()).effective
    assert rows["geology.drilling.II"].qty == 172
    assert rows["geology.drilling.IV"].qty == 602


def test_geodesy_topo_plan_row_prefills_area_from_last_survey_row(isolated_smeta_db):
    at = open_app()

    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)

    at.selectbox(key="group_geodesy_topo_surveys_0_topo_scale").set_value("1:500")
    at.number_input(key="group_geodesy_topo_surveys_0_topo_area").set_value(2.0)
    at.run(timeout=30)

    at.button(key="add_geodesy_topo_plans").click()
    at.run(timeout=30)

    assert at.exception == []
    # новая строка плана (row_id=1, т.к. row_id=0 — незаполненная
    # строка-заглушка, созданная при первом рендере группы) стартует с
    # площади съёмки С ТЕМ ЖЕ масштабом (единственная съёмка — 1:500,
    # новая строка плана по умолчанию тоже 1:500, первый вариант
    # списка) — удобный дефолт, не жёсткая привязка (см.
    # app/streamlit_app.py::sub_default)
    assert at.number_input(key="group_geodesy_topo_plans_1_topo_area").value == 2.0


def test_geodesy_topo_plan_prefill_matches_by_scale_not_by_insertion_order(isolated_smeta_db):
    # Регрессия на реальный кейс объекта Объект «Тест»: три съёмки
    # РАЗНЫХ масштабов введены в этом порядке — 1:500, 1:1000, 1:5000
    # (последняя — 1:5000). При добавлении плана 1:500 площадь должна
    # подтянуться от съёмки 1:500 (единственная строка группы плана
    # доступна оператору со значением масштаба "1:500" по умолчанию —
    # первый вариант стандартного ряда), а НЕ от последней введённой
    # съёмки (1:5000) — раньше ошибочно матчилось именно так.
    at = open_app()

    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)

    at.selectbox(key="group_geodesy_topo_surveys_0_topo_scale").set_value("1:500")
    at.number_input(key="group_geodesy_topo_surveys_0_topo_area").set_value(2.0)
    at.run(timeout=30)

    at.button(key="add_geodesy_topo_surveys").click()
    at.run(timeout=30)
    at.selectbox(key="group_geodesy_topo_surveys_1_topo_scale").set_value("1:1000")
    at.number_input(key="group_geodesy_topo_surveys_1_topo_area").set_value(147.0)
    at.run(timeout=30)

    at.button(key="add_geodesy_topo_surveys").click()
    at.run(timeout=30)
    at.selectbox(key="group_geodesy_topo_surveys_2_topo_scale").set_value("1:5000")
    at.number_input(key="group_geodesy_topo_surveys_2_topo_area").set_value(500.0)
    at.run(timeout=30)

    # Строка плана-заглушки (row_id=0) отрисовалась ЕЩЁ ДО того, как
    # были введены съёмки (в момент первого переключения на геодезию)
    # — её дефолт площади уже зафиксирован тогда (площадь ещё не
    # введена ни у одной съёмки) и больше не пересчитывается (см.
    # докстринг render_group_field про sub_default — только на самом
    # первом рендере виджета). Поэтому новую строку плана добавляем
    # ЯВНО кнопкой, уже после того как все три съёмки заполнены — она
    # рендерится впервые именно сейчас и должна сматчиться по
    # масштабу 1:500, а НЕ по последней введённой съёмке (1:5000).
    at.button(key="add_geodesy_topo_plans").click()
    at.run(timeout=30)

    assert at.exception == []
    assert at.selectbox(key="group_geodesy_topo_plans_1_topo_scale").value == "1:500"
    assert at.number_input(key="group_geodesy_topo_plans_1_topo_area").value == 2.0


def test_override_form_does_not_silently_truncate_fractional_class_v_quantity(isolated_smeta_db):
    # Регрессия на риск, найденный при исправлении целочисленного
    # ввода: строки класса В геологии (unit="проба") ДЕКЛАРАТИВНО
    # целые (core/formatting.py::INTEGER_UNITS), но их qty реально
    # дробный (типовой профиль × число скважин, см.
    # docs/predmetnaya_oblast.md) — форма правки НЕ должна молча
    # обрезать такое значение до целого при простом открытии/расчёте.
    at = open_app()

    at.number_input(key="group_geology_drilling_by_category_0_drilling_length").set_value(450)
    at.selectbox(key="group_geology_drilling_by_category_0_soil_category").set_value("IV")
    at.number_input(key="field_geology_drilling_count").set_value(30)
    at.run(timeout=30)

    # Ведомость (этап 1) строится сразу по заполненной форме — формы
    # правки строк класса В теперь на этом этапе.
    assert at.exception == []
    # monolity_dispersnye = MONOLITY_DISPERSNYE_PER_HOLE(2.48) * 30 = 74.4 —
    # unit="проба" (целая по словарю), но значение реально дробное.
    qty_widgets = [w for w in at.number_input if w.label == "Кол-во"]
    assert any(w.value == pytest.approx(74.4) for w in qty_widgets), (
        "форма правки обрезала дробное qty строки класса В до целого "
        f"при открытии — значения: {[w.value for w in qty_widgets]}"
    )


def test_calculating_geodesy_shows_estimate_table(isolated_smeta_db):
    at = open_app()

    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)
    at.number_input(key="field_geodesy_oporny_points_count").set_value(8)
    at.number_input(key="field_geodesy_privyazka_count").set_value(50)
    at.selectbox(key="group_geodesy_topo_surveys_0_topo_scale").set_value("1:500")
    at.number_input(key="group_geodesy_topo_surveys_0_topo_area").set_value(2.0)
    at.run(timeout=30)

    # Геодезия — двухэтапная: этап 1 — только ведомость (строки групп
    # съёмки/плана — её строки), правок строк нет (всё — ввод формы).
    assert at.exception == []
    assert len(at.dataframe) == 1
    assert not [w for w in at.number_input if w.label == "Кол-во"]

    at.button(key="confirm_geodesy_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.exception == []
    # этап 2: утверждённая ведомость + смета; qty не правится ни у одной
    # строки (у транспорта — доли от базы — правится ставка)
    assert len(at.dataframe) == 2
    assert not [w for w in at.number_input if w.label == "Кол-во"]
    assert len([w for w in at.number_input if w.label == "Ставка (доля от базы)"]) == 1
    session = at.session_state["staged_sessions"]["geodesy"]
    assert session.result("volumes").lines["topo_survey.0"].qty == 2.0


# --- экология (4-й модуль) ---


def test_ecology_count_fields_render_as_integer_widgets(isolated_smeta_db):
    at = open_app()

    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)

    for key in (
        "field_ecology_radon_points",
        "field_ecology_physical_factors_points",
        "field_ecology_data_acquisition_count",
    ):
        widget = at.number_input(key=key)
        assert widget.step == 1, f"{key}: step={widget.step!r}, ожидался 1 (целое число)"

    # площадь радиационного обследования — дробная (не integer=True)
    area_widget = at.number_input(key="field_ecology_radiation_area")
    assert area_widget.step == 0.01


def test_ecology_coefficient_fields_start_at_field_default_not_zero(isolated_smeta_db):
    # Field.default: коэффициент 0 обнулил бы строку — поле должно
    # стартовать с 1, а обычные поля — по-прежнему с 0. Параметры
    # расценки — поля этапа сметы (решение №12), на этапе ведомости их нет.
    at = open_app()
    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)
    at.number_input(key="field_ecology_radiation_area").set_value(760)
    at.number_input(key="field_ecology_radon_points").set_value(200)
    at.number_input(key="field_ecology_physical_factors_points").set_value(10)
    at.number_input(key="field_ecology_data_acquisition_count").set_value(26)
    at.run(timeout=30)
    at.selectbox(key="group_ecology_sampling_0_sample_type").set_value("soil_chemical")
    at.number_input(key="group_ecology_sampling_0_sample_count").set_value(160)
    at.run(timeout=30)

    stage_one_keys = {w.key for w in at.number_input} | {w.key for w in at.selectbox}
    assert "field_ecology_route_kameral_coefficient" not in stage_one_keys
    assert "field_ecology_passability" not in stage_one_keys

    at.button(key="confirm_ecology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.exception == []

    assert at.number_input(key="field_ecology_estimate_route_kameral_coefficient").value == 1.0
    assert at.number_input(key="field_ecology_estimate_route_field_coefficient").value == 1.0
    assert at.number_input(key="field_ecology_estimate_lab_total_base").value == 0.0
    assert at.selectbox(key="field_ecology_estimate_passability").value == "good"
    # два поля с dynamic_options на одной форме — у каждого свои подписи.
    # Само позднее связывание labels_by_value в format_func ловит
    # test_ecology_sampling_group_add_row_and_confirm (падает без фикса).
    subbase = at.selectbox(key="field_ecology_estimate_subbase_item")
    assert subbase.value == "13"
    assert all(label.startswith("Т80 п.") for label in subbase.options)
    assert at.selectbox(key="field_ecology_estimate_passability").options == [
        "Хорошая", "Удовлетворительная",
    ]


def test_ecology_sampling_group_add_row_and_confirm(isolated_smeta_db):
    at = open_app()
    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)
    at.number_input(key="field_ecology_radiation_area").set_value(760)
    at.number_input(key="field_ecology_radon_points").set_value(200)
    at.number_input(key="field_ecology_physical_factors_points").set_value(10)
    at.number_input(key="field_ecology_data_acquisition_count").set_value(26)
    at.run(timeout=30)

    # первая (заглушка) строка группы "Опробование окружающей среды"
    at.selectbox(key="group_ecology_sampling_0_sample_type").set_value("soil_chemical")
    at.number_input(key="group_ecology_sampling_0_sample_count").set_value(160)
    at.run(timeout=30)

    at.button(key="add_ecology_sampling").click()
    at.run(timeout=30)
    at.selectbox(key="group_ecology_sampling_1_sample_type").set_value("surface_water_chemical")
    at.number_input(key="group_ecology_sampling_1_sample_count").set_value(3)
    at.run(timeout=30)

    at.button(key="confirm_ecology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")

    assert at.exception == []
    session = at.session_state["staged_sessions"]["ecology"]
    volumes = session.result("volumes").lines
    assert volumes["sampling_soil_chemical"].qty == 160
    assert volumes["sampling_surface_water_chemical"].qty == 3
    estimate = session.compute("estimate", ReferenceBook()).effective
    assert estimate["ecology.sampling.soil_chemical"].qty == 160
    # утверждённая ведомость + смета
    assert len(at.dataframe) == 2
    # qty на этапе сметы правится только у камералки лабораторных; у
    # строк-долей (транспорт, техотчёт) — ставка
    assert len([w for w in at.number_input if w.label == "Кол-во"]) == 1
    assert len([w for w in at.number_input if w.label == "Ставка (доля от базы)"]) >= 1


def test_opening_old_single_stage_ecology_smeta_splits_fields_by_stage(isolated_smeta_db):
    SmetaStore().save_smeta(
        "старая экология", "ecology",
        {
            "radiation_area": 760, "radon_points": 200, "physical_factors_points": 10,
            "data_acquisition_count": 26,
            "sampling": [{"sample_type": "soil_chemical", "sample_count": 160}],
            "passability": "satisfactory", "lab_total_base": 5000,
        },
    )
    at = open_app("versii")
    for button in at.button:
        if button.label == "Открыть":
            button.click()
    at.run(timeout=30)
    goto(at, "vedomost")

    assert at.exception == []
    session = at.session_state["staged_sessions"]["ecology"]
    assert session.inputs["estimate"] == {"passability": "satisfactory", "lab_total_base": 5000}
    assert "passability" not in session.inputs["volumes"]
    assert at.number_input(key="field_ecology_radiation_area").value == 760

    at.button(key="confirm_ecology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.selectbox(key="field_ecology_estimate_passability").value == "satisfactory"
    assert at.number_input(key="field_ecology_estimate_lab_total_base").value == 5000


# --- геология: многоэтапный сценарий (ведомость → смета) ---


def _fill_geology(at):
    at.number_input(key="group_geology_drilling_by_category_0_drilling_length").set_value(450)
    at.selectbox(key="group_geology_drilling_by_category_0_soil_category").set_value("IV")
    at.number_input(key="field_geology_drilling_count").set_value(30)
    at.run(timeout=30)


def test_geology_volume_sheet_is_shown_before_any_confirmation(isolated_smeta_db):
    at = open_app()
    _fill_geology(at)

    assert at.exception == []
    session = at.session_state["staged_sessions"]["geology"]
    assert session.current == "volumes"
    assert session.status("volumes") == STATUS_DRAFT
    # на экране этапа 1 — только таблица ведомости, сметы ещё нет
    assert len(at.dataframe) == 1
    assert "Источник" in at.dataframe[0].value.columns
    assert "Цена, руб." not in at.dataframe[0].value.columns


def test_geology_full_stage_flow_with_return_marks_estimate_stale(isolated_smeta_db):
    at = open_app()
    _fill_geology(at)

    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    session = at.session_state["staged_sessions"]["geology"]
    assert session.current == "estimate"
    assert session.status("volumes") == STATUS_CONFIRMED
    # на экране сметы: утверждённая ведомость (только чтение) + смета
    # ведомость + смета; плюс 2 таблицы раздела себестоимости геологии (Э6)
    assert len([d for d in at.dataframe if "Статья" not in d.value.columns
                and "Объект" not in d.value.columns]) == 2

    at.button(key="confirm_geology_estimate").click()
    at.run(timeout=30)
    session = at.session_state["staged_sessions"]["geology"]
    assert session.last_stage_status() == STATUS_CONFIRMED

    # две версии в цепочке: смета — дочерняя от ведомости
    versions = SmetaStore().list_versions("geology")
    assert [row[4] for row in versions] == ["estimate", "volumes"]
    assert versions[0][1] == versions[1][0]

    # возврат на ведомость: смета помечена устаревшей, но не удалена
    at.button(key="reopen_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "vedomost")
    assert at.exception == []
    session = at.session_state["staged_sessions"]["geology"]
    assert session.current == "volumes"
    assert session.status("estimate") == STATUS_STALE
    assert session.result("estimate") is not None


def test_geology_estimate_stage_does_not_offer_qty_edit_for_volume_lines(isolated_smeta_db):
    at = open_app()
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")

    assert at.exception == []
    # "Кол-во" на этапе сметы геологии не правится ни у одной строки:
    # строки без своей строки ведомости (транспорт, техотчёт, камеральная
    # обработка хим.состава воды и — с Э6а — лабораторных песчаных т.86 п.2)
    # — доли от базы, у них правится ставка.
    qty_widgets = [w for w in at.number_input if w.label == "Кол-во"]
    assert qty_widgets == []
    rate_widgets = [w for w in at.number_input if w.label == "Ставка (доля от базы)"]
    assert len(rate_widgets) == 4
    price_widgets = [w for w in at.number_input if w.label == "Цена"]
    assert len(price_widgets) > len(rate_widgets)


def test_geology_opening_saved_version_restores_confirmed_stages(isolated_smeta_db):
    at = open_app()
    at.text_input(key="save_smeta_name").set_value("ГОК тест")
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")

    # новая вкладка браузера: пустое состояние, открываем версию
    at2 = open_app("versii")
    for button in at2.button:
        if button.label == "Открыть версию":
            button.click()
    at2.run(timeout=30)
    # открытие само переводит на страницу текущего этапа — смету
    assert any(h.value.startswith("3. Максимальный бюджет") for h in at2.header)
    goto(at2, "smeta")

    assert at2.exception == []
    session = at2.session_state["staged_sessions"]["geology"]
    assert session.status("volumes") == STATUS_CONFIRMED
    assert session.current == "estimate"
    assert session.result("volumes").lines["drilling_catIV"].qty == 450
    assert at2.text_input(key="save_smeta_name").value == "ГОК тест"


def test_all_modules_are_two_stage_without_calculate_button(isolated_smeta_db):
    at = open_app()
    for code in ("geology", "geophysics", "geodesy", "ecology"):
        at.radio(key="module_choice").set_value(code)
        at.run(timeout=30)
        labels = [b.label for b in at.button]
        assert "Рассчитать" not in labels
        assert "Подтвердить ведомость" in labels
    assert at.exception == []


def test_opening_old_single_stage_geology_smeta_goes_to_volume_stage(isolated_smeta_db):
    # Смета геологии, сохранённая прежним одноэтапным способом: ввод
    # попадает в этап ведомости, правки — в этап сметы; правка объёма
    # строки с ведомостью там не применяется, но и не теряется.
    from core.overrides import Edit

    store = SmetaStore()
    smeta_id = store.save_smeta(
        "старая", "geology",
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30},
    )
    store.save_edits(smeta_id, [Edit("geology.geodesy_privyazka", "qty", 30, 31, None)])

    at = open_app("versii")
    for button in at.button:
        if button.label == "Открыть":
            button.click()
    at.run(timeout=30)
    goto(at, "vedomost")

    assert at.exception == []
    session = at.session_state["staged_sessions"]["geology"]
    assert session.current == "volumes"
    assert at.number_input(key="field_geology_drilling_count").value == 30

    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    session = at.session_state["staged_sessions"]["geology"]
    draft = session.compute("estimate", ReferenceBook())
    assert [(e.line_id, e.field) for e in draft.not_applied] == [("geology.geodesy_privyazka", "qty")]
    assert at.exception == []


def test_opening_saved_smeta_refills_form_of_currently_shown_module(isolated_smeta_db):
    # Регрессия (была и до многоэтапности): смета открывается, когда
    # форма этого же модуля уже на экране — Streamlit игнорировал
    # value= у виджета с уже существующим key, и форма показывала
    # прежние значения вместо открытых.
    SmetaStore().save_smeta("старая", "geophysics", {"vez_count": 80, "mpv_count": 400})

    at = open_app()
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    goto(at, "versii")
    for button in at.button:
        if button.label == "Открыть":
            button.click()
    at.run(timeout=30)
    goto(at, "vedomost")

    assert at.exception == []
    assert at.number_input(key="field_geophysics_vez_count").value == 80
    assert at.number_input(key="field_geophysics_mpv_count").value == 400


# --- геофизика: два этапа ---


def test_geophysics_stage_flow_and_locked_quantities(isolated_smeta_db):
    at = open_app()
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    at.number_input(key="field_geophysics_vez_count").set_value(80)
    at.number_input(key="field_geophysics_mpv_count").set_value(400)
    at.run(timeout=30)

    # этап 1: ведомость, камералка МПВ правится строкой (ВЭЗ/МПВ — в форме)
    assert at.exception == []
    assert [w.label for w in at.number_input if w.label == "Кол-во"] == ["Кол-во"]

    at.button(key="confirm_geophysics_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.exception == []
    session = at.session_state["staged_sessions"]["geophysics"]
    assert session.status("volumes") == STATUS_CONFIRMED
    # этап 2: qty не правится; у транспорта (доля от базы) — ставка
    assert not [w for w in at.number_input if w.label == "Кол-во"]
    assert len([w for w in at.number_input if w.label == "Ставка (доля от базы)"]) == 1

    at.button(key="confirm_geophysics_estimate").click()
    at.run(timeout=30)
    assert at.session_state["staged_sessions"]["geophysics"].last_stage_status() == STATUS_CONFIRMED


# --- черновик: сохранение без подтверждения, на любом этапе ---


def _click(at, label):
    for button in at.button:
        if button.label == label:
            button.click()
    at.run(timeout=30)


def test_draft_can_be_saved_with_empty_required_fields_and_reopened(isolated_smeta_db):
    at = open_app()
    at.text_input(key="save_smeta_name").set_value("ГОК черновик")
    # обязательные поля не заполнены (метраж 0), число скважин — есть
    at.number_input(key="field_geology_drilling_count").set_value(12)
    at.run(timeout=30)

    at.button(key="save_draft_geology").click()
    at.run(timeout=30)
    assert at.exception == []
    store = SmetaStore()
    assert len(store.list_drafts()) == 1
    assert store.list_versions() == []

    # повторное сохранение того же имени — перезапись, не накопление
    at.number_input(key="field_geology_drilling_count").set_value(15)
    at.run(timeout=30)
    at.button(key="save_draft_geology").click()
    at.run(timeout=30)
    assert len(store.list_drafts()) == 1

    at2 = open_app("versii")
    _click(at2, "Открыть черновик")
    # открытие само переводит на страницу этапа черновика — ведомость
    assert any(h.value == "2. Ведомость объёмов" for h in at2.header)
    goto(at2, "vedomost")
    assert at2.exception == []
    session = at2.session_state["staged_sessions"]["geology"]
    assert session.current == "volumes"
    assert at2.number_input(key="field_geology_drilling_count").value == 15
    assert at2.text_input(key="save_smeta_name").value == "ГОК черновик"


def test_draft_on_estimate_stage_restores_confirmed_volumes_and_parameters(isolated_smeta_db):
    at = open_app()
    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)
    at.number_input(key="field_ecology_radiation_area").set_value(760)
    at.number_input(key="field_ecology_radon_points").set_value(200)
    at.number_input(key="field_ecology_physical_factors_points").set_value(10)
    at.number_input(key="field_ecology_data_acquisition_count").set_value(26)
    at.selectbox(key="group_ecology_sampling_0_sample_type").set_value("soil_chemical")
    at.number_input(key="group_ecology_sampling_0_sample_count").set_value(160)
    at.run(timeout=30)
    at.button(key="confirm_ecology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")

    # этап сметы: параметр введён, смета НЕ подтверждена — черновик
    at.number_input(key="field_ecology_estimate_lab_total_base").set_value(5000)
    at.run(timeout=30)
    at.button(key="save_draft_ecology").click()
    at.run(timeout=30)
    assert at.exception == []
    assert len(SmetaStore().list_versions("ecology")) == 1  # только ведомость

    at2 = open_app("versii")
    _click(at2, "Открыть черновик")
    assert any(h.value.startswith("3. Максимальный бюджет") for h in at2.header)
    goto(at2, "smeta")
    assert at2.exception == []
    session = at2.session_state["staged_sessions"]["ecology"]
    assert session.status("volumes") == STATUS_CONFIRMED
    assert session.result("volumes").lines["radiation_area"].qty == 760
    assert session.current == "estimate"
    assert session.last_stage_status() == STATUS_DRAFT  # в бюджет не идёт
    assert at2.number_input(key="field_ecology_estimate_lab_total_base").value == 5000


# --- дата расчёта и политика индекса (Э1 дорожной карты) ---


def _geology_to_estimate(at):
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")


def _texts(elements):
    return [element.value for element in elements]


def test_calc_date_defaults_to_today(isolated_smeta_db):
    import datetime

    at = open_app()
    assert at.date_input(key="calc_date").value == datetime.date.today()
    assert at.checkbox(key="index_strict").value is False


def test_date_after_known_period_shows_total_with_warning(isolated_smeta_db):
    import datetime

    at = open_app()
    at.date_input(key="calc_date").set_value(datetime.date(2026, 10, 25))
    at.run(timeout=30)
    _geology_to_estimate(at)

    assert at.exception == []
    warnings = _texts(at.warning)
    assert any("III кв. 2026" in w and "25.10.2026" in w and "не внесён" in w for w in warnings)
    assert any("Итого с индексацией (×81,95, III кв. 2026)" in m for m in _texts(at.markdown))


def test_strict_index_mode_shows_no_total(isolated_smeta_db):
    import datetime

    at = open_app()
    at.date_input(key="calc_date").set_value(datetime.date(2026, 10, 25))
    at.checkbox(key="index_strict").check()
    at.run(timeout=30)
    _geology_to_estimate(at)

    assert at.exception == []
    markdown = _texts(at.markdown)
    assert any("недоступен" in m for m in markdown)
    assert not any("Итого с индексацией" in m for m in markdown)


def test_date_inside_period_has_no_index_warning(isolated_smeta_db):
    import datetime

    at = open_app()
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    _geology_to_estimate(at)

    assert at.exception == []
    assert not any("не внесён" in w for w in _texts(at.warning))
    assert any("на 30.09.2026" in m for m in _texts(at.markdown))


def test_calc_date_saved_with_version_and_restored_on_open(isolated_smeta_db):
    import datetime

    at = open_app()
    at.text_input(key="save_smeta_name").set_value("ГОК дата")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    _geology_to_estimate(at)
    assert at.exception == []
    version_id = at.session_state["staged_sessions"]["geology"].version_id
    assert SmetaStore().get_version_calc_date(version_id) == "2026-09-30"

    # новая вкладка: дата по умолчанию — сегодня, после открытия — дата версии
    at2 = open_app("versii")
    _click(at2, "Открыть версию")
    assert at2.exception == []
    assert at2.date_input(key="calc_date").value == datetime.date(2026, 9, 30)
    assert any("дата расчёта 30.09.2026" in i for i in _texts(at2.info))


@pytest.mark.parametrize(
    "calc_date, vat_text",
    [
        # версия на дату 2026 г. (в т.ч. сохранённая до 30.10.2026, когда
        # программа считала 20 %) открывается и считается по 22 %
        ("2026-09-30", "НДС 22 % — 425-ФЗ"),
        # версия на дату 2025 г.: открывается без ошибок; индекса на эту дату
        # в справочнике нет — итога с НДС нет, предупреждение вместо падения
        ("2025-12-31", None),
    ],
)
def test_saved_version_recomputes_vat_by_its_own_calc_date(isolated_smeta_db, calc_date, vat_text):
    import datetime

    import run_budget_stub
    from core.aggregator import BudgetAggregator

    day = datetime.date.fromisoformat(calc_date)
    at = open_app()
    at.text_input(key="save_smeta_name").set_value("НДС версия")
    at.date_input(key="calc_date").set_value(day)
    at.run(timeout=30)
    _geology_to_estimate(at)
    assert at.exception == []

    at2 = open_app("versii")
    _click(at2, "Открыть версию")
    goto(at2, "smeta")
    assert at2.exception == []
    assert at2.date_input(key="calc_date").value == day
    captions = _texts(at2.caption)
    if vat_text is None:
        assert any("недоступен" in m for m in _texts(at2.markdown))
        assert not any(c.startswith("НДС ") for c in captions)
        return
    expected = run_budget_stub.build_budget(BudgetAggregator(), ReferenceBook(), calc_date)
    geology = next(m for m in expected.summary()["modules"] if m.module_code == "geology")
    assert geology.total_with_vat == pytest.approx(geology.indexed_total * 1.22)
    assert any(rub(geology.total_with_vat) in m for m in _texts(at2.markdown))
    assert any(c.startswith(vat_text) for c in captions)


# --- правка ставки у строки «доля от базы» (Э2 дорожной карты) ---


def test_rate_edit_of_transport_line_through_interface(isolated_smeta_db):
    at = open_app()
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    at.number_input(key="field_geophysics_vez_count").set_value(80)
    at.number_input(key="field_geophysics_mpv_count").set_value(400)
    at.run(timeout=30)
    at.button(key="confirm_geophysics_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")

    form_id = "edit_form_geophysics_estimate_geophysics.transport"
    rate = [w for w in at.number_input if w.label == "Ставка (доля от базы)" and w.form_id == form_id]
    assert len(rate) == 1 and rate[0].value == pytest.approx(0.05)
    rate[0].set_value(0.06)
    [b for b in at.button if b.form_id == form_id][0].click()
    at.run(timeout=30)

    assert at.exception == []
    session = at.session_state["staged_sessions"]["geophysics"]
    edits = session.overrides["estimate"].history()
    assert [(e.line_id, e.field, e.new_value) for e in edits] == [("geophysics.transport", "rate", 0.06)]
    draft = session.compute("estimate", ReferenceBook())
    assert draft.not_applied == []
    assert draft.effective["geophysics.transport"].coefs["rate"] == 0.06


# --- сводный бюджет в интерфейсе (Э3 дорожной карты) ---


def _budget_table(at):
    tables = [d.value for d in at.dataframe if "Итого с НДС" in getattr(d.value, "columns", [])]
    assert len(tables) == 1
    return tables[0]


def _geology_confirmed_estimate(at):
    _geology_to_estimate(at)
    at.button(key="confirm_geology_estimate").click()
    at.run(timeout=30)


def test_budget_section_empty_before_any_confirmation(isolated_smeta_db):
    at = open_app("svodny")
    assert at.exception == []
    assert any(h.value == "5. Сводный бюджет" for h in at.header)
    assert any("бюджет пуст" in i for i in _texts(at.info))
    # с 30.10.2026 кнопка выгрузки видна всегда (главное действие страницы,
    # docs/ui_guide.md), но недоступна, пока бюджет пуст — скачать нечего
    buttons = [b for b in at.get("download_button") if b.proto.label.startswith("Скачать сводный")]
    assert len(buttons) == 1 and buttons[0].proto.disabled


def test_budget_section_includes_only_confirmed_modules(isolated_smeta_db):
    import datetime

    import run_budget_stub
    from core.aggregator import BudgetAggregator

    at = open_app()
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    _geology_confirmed_estimate(at)
    # геофизика: только ведомость подтверждена — в бюджет не входит
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    at.number_input(key="field_geophysics_vez_count").set_value(80)
    at.number_input(key="field_geophysics_mpv_count").set_value(400)
    at.run(timeout=30)
    at.button(key="confirm_geophysics_volumes").click()
    at.run(timeout=30)
    goto(at, "svodny")
    assert at.exception == []

    table = _budget_table(at)
    assert list(table["Раздел"]) == ["Геология (ОИГ)", "ИТОГО"]
    assert any("Геофизика (ИГФИ): смета ещё не подтверждена" in i for i in _texts(at.info))

    # итог геологии = итог run_budget_stub на ту же дату (те же входные данные)
    expected = run_budget_stub.build_budget(BudgetAggregator(), ReferenceBook(), "2026-09-30")
    geology = next(m for m in expected.summary()["modules"] if m.module_code == "geology")
    assert table["Итого с НДС"].iloc[0] == pytest.approx(round(geology.total_with_vat, 2))
    assert any(rub(geology.total_with_vat) in m for m in _texts(at.markdown))

    labels = [b.proto.label for b in at.get("download_button")]
    assert "Скачать сводный бюджет (Excel)" in labels


def test_budget_excludes_module_after_return_to_volume_sheet(isolated_smeta_db):
    at = open_app()
    _geology_confirmed_estimate(at)
    goto(at, "svodny")
    assert list(_budget_table(at)["Раздел"]) == ["Геология (ОИГ)", "ИТОГО"]

    goto(at, "smeta")
    at.button(key="reopen_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "svodny")
    assert at.exception == []
    assert any("Геология (ОИГ)" in i and "в бюджет не вошёл" in i for i in _texts(at.info))


# --- открытие несовместимой версии, имя черновика (Э4 дорожной карты) ---


def test_opening_version_with_unreadable_snapshot_shows_error(isolated_smeta_db):
    import json as _json
    import sqlite3 as _sqlite3

    at = open_app()
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    path = persistence_module.DEFAULT_DB_PATH
    conn = _sqlite3.connect(path)
    items = _json.loads(conn.execute("SELECT lines FROM smeta_versions").fetchone()[0])
    items[0]["v"] = 99
    conn.execute("UPDATE smeta_versions SET lines = ?", (_json.dumps(items),))
    conn.commit()
    conn.close()

    at2 = open_app("versii")
    _click(at2, "Открыть версию")
    assert at2.exception == []
    errors = [e.value for e in at2.error]
    assert any("Не удалось открыть версию" in e and "v99" in e for e in errors)
    # версия не подставилась: сессии геологии нет (страница «Версии» её не
    # создаёт) или она пустая; на странице ведомости — пустая форма
    session = at2.session_state["staged_sessions"].get("geology")
    assert session is None or (session.version_id is None and session.result("volumes") is None)
    goto(at2, "vedomost")
    session = at2.session_state["staged_sessions"]["geology"]
    assert session.version_id is None and session.result("volumes") is None


def test_draft_without_name_gets_dated_name(isolated_smeta_db):
    at = open_app()
    at.number_input(key="field_geology_drilling_count").set_value(5)
    at.run(timeout=30)
    at.button(key="save_draft_geology").click()
    at.run(timeout=30)
    assert at.exception == []
    name = at.text_input(key="save_smeta_name").value
    assert name.startswith("Геология (ОИГ), черновик ")
    drafts = SmetaStore().list_drafts()
    assert [row[1] for row in drafts] == [name]


# --- данные объекта, шаг 0 (Э5 дорожной карты) ---


def test_object_block_rendered_before_modules_and_empty_by_default(isolated_smeta_db):
    # «До модулей» теперь — первая страница меню, открывается по умолчанию
    at = open_app("obekt")
    assert at.exception == []
    assert any(h.value == "1. Объект и ТЗ" for h in at.header)
    assert at.text_input(key="obj_code").value == ""
    assert at.number_input(key="obj_distance_km").value is None
    assert at.checkbox(key="obj_unfavorable_period").value is False


def test_object_distance_selects_transport_item_and_is_saved_with_version(isolated_smeta_db):
    at = open_app("obekt")
    at.text_input(key="obj_code").set_value("Т-01")
    at.number_input(key="obj_distance_km").set_value(22.0)
    at.run(timeout=30)
    goto(at, "vedomost")
    _geology_to_estimate(at)
    assert at.exception == []
    session = at.session_state["staged_sessions"]["geology"]
    draft = session.compute("estimate", _ctx(22.0))
    assert draft.effective["geology.transport"].coefs["rate"] == pytest.approx(0.1875)
    assert not any("расстояние до объекта не задано" in w for w in _texts(at.warning))

    at.button(key="confirm_geology_estimate").click()
    at.run(timeout=30)
    version_id = at.session_state["staged_sessions"]["geology"].version_id
    saved = SmetaStore().get_version_object_data(version_id)
    assert saved["code"] == "Т-01" and saved["distance_km"] == 22.0
    confirmed = at.session_state["staged_sessions"]["geology"].result("estimate").lines
    assert confirmed["geology.transport"].coefs["rate"] == pytest.approx(0.1875)

    # новая вкладка: данные объекта пустые, после открытия версии — восстановлены
    # (открывается на странице «Версии», данные видны на странице «Объект и ТЗ»)
    at2 = open_app("obekt")
    assert at2.text_input(key="obj_code").value == ""
    goto(at2, "versii")
    _click(at2, "Открыть версию")
    assert at2.exception == []
    goto(at2, "obekt")
    assert at2.text_input(key="obj_code").value == "Т-01"
    assert at2.number_input(key="obj_distance_km").value == 22.0


def test_section_transport_distance_on_object_page_is_used_and_saved(isolated_smeta_db):
    """Расстояние для внутреннего транспорта по разделу (боевой прогон 31.10.2026):
    поля на странице «1. Объект и ТЗ», одна главная кнопка; расстояние геологии
    перекрывает общее; хранится с версией и восстанавливается при открытии."""
    from tests.test_ui_guide import primary_buttons

    at = open_app("obekt")
    assert at.number_input(key="obj_trd_geology").value is None
    assert at.number_input(key="obj_trd_ecology").value is None
    at.number_input(key="obj_distance_km").set_value(12.0)
    at.number_input(key="obj_trd_geology").set_value(22.0)
    at.number_input(key="obj_trd_ecology").set_value(12.0)
    at.run(timeout=30)
    assert at.exception == []
    assert primary_buttons(at) == [("Перейти к ведомости объёмов", True)]

    goto(at, "vedomost")
    _geology_to_estimate(at)
    assert at.exception == []
    at.button(key="confirm_geology_estimate").click()
    at.run(timeout=30)
    session = at.session_state["staged_sessions"]["geology"]
    transport = session.result("estimate").lines["geology.transport"]
    assert transport.coefs["rate"] == pytest.approx(0.1875)  # п.5 по 22 км раздела, не п.4 по 12
    assert "расстояние раздела «Геология»" in transport.basis
    saved = SmetaStore().get_version_object_data(session.version_id)
    assert saved["transport_distance_overrides"] == {"geology": 22.0, "ecology": 12.0}

    at2 = open_app("obekt")
    goto(at2, "versii")
    _click(at2, "Открыть версию")
    goto(at2, "obekt")
    assert at2.exception == []
    assert at2.number_input(key="obj_trd_geology").value == 22.0
    assert at2.number_input(key="obj_trd_ecology").value == 12.0


def _ctx(distance):
    from core.object_data import CalculationContext, ObjectData

    return CalculationContext(ReferenceBook(), ObjectData(distance_km=distance))


def test_without_distance_geology_warns_and_keeps_etalon_transport(isolated_smeta_db):
    at = open_app()
    _geology_to_estimate(at)
    assert at.exception == []
    assert any("расстояние до объекта не задано" in w for w in _texts(at.warning))
    lines = at.session_state["staged_sessions"]["geology"].compute("estimate", ReferenceBook()).effective
    assert lines["geology.transport"].coefs["rate"] == pytest.approx(0.0875)


def test_unfavorable_period_flag_only_warns(isolated_smeta_db):
    at = open_app("obekt")
    at.checkbox(key="obj_unfavorable_period").check()
    at.run(timeout=30)
    assert at.exception == []
    assert any("неблагоприятный период" in w and "применяется у геологии" in w for w in _texts(at.warning))


def test_object_data_survives_module_switch(isolated_smeta_db):
    # и переход между страницами меню: поле на «Объект и ТЗ», модули — на
    # «Ведомость объёмов»
    at = open_app("obekt")
    at.text_input(key="obj_name").set_value("Карьер")
    at.run(timeout=30)
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)
    at.radio(key="module_choice").set_value("geology")
    at.run(timeout=30)
    assert any("Карьер" in c for c in _texts(at.sidebar.caption))
    goto(at, "obekt")
    assert at.text_input(key="obj_name").value == "Карьер"


# --- себестоимость геологии (Э6) и контроль по архиву (Э7) ---


def _set_cost(at, **values):
    for name, value in values.items():
        at.number_input(key=f"cost_{name}").set_value(value)
    at.run(timeout=30)


FULL_COST = dict(
    brigades=3, itr=2, drivers=2, cooks=1, salary_worker=50000.0, salary_itr=50000.0,
    salary_driver=50000.0, salary_cook=50000.0, fuel_price_diesel=70.0, lab_samples=10,
)


def test_cost_section_waits_for_confirmed_volume_sheet(isolated_smeta_db):
    at = open_app("minimum")
    assert at.exception == []
    assert any("после подтверждения ведомости" in i for i in _texts(at.info))


def test_cost_section_incomplete_without_salaries(isolated_smeta_db):
    at = open_app()
    _geology_to_estimate(at)
    goto(at, "minimum")
    _set_cost(at, brigades=3)
    assert at.exception == []
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Коэффициент снижения"] == "—"
    assert any("Не заполнено" in i and "оклад: буровые рабочие" in i for i in _texts(at.info))


def test_cost_section_full_gives_three_numbers_and_is_saved(isolated_smeta_db):
    import datetime

    from modules.geology.cost import CostInputs, compute_cost

    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.number_input(key="obj_distance_km").set_value(22.0)
    at.run(timeout=30)
    goto(at, "vedomost")
    _geology_to_estimate(at)
    goto(at, "minimum")
    _set_cost(at, **FULL_COST)
    assert at.exception == []

    metrics = {m.label: m.value for m in at.metric}
    expected = compute_cost(CostInputs(**{k: v for k, v in FULL_COST.items()}), 450, distance_km=22.0)
    assert expected.complete
    assert metrics["Минимум: себестоимость"] == money_text(expected.cost)
    assert metrics["Коэффициент снижения"] != "—"
    # минимум не сверен с фактом — коэффициент только ориентир (Э8 (б), аудит 30.10)
    assert any("Минимум не калиброван" in w for w in _texts(at.warning))
    # смета СБЦ при этом не изменилась: транспорт по расстоянию 22 км — как в Э5
    from modules.geology.cost import archive_by_proximity, load_archive

    nearest = [o for o in archive_by_proximity(load_archive()[0], 2026) if o.fact_cost_per_m is not None][0]
    assert any("ближайших по дате" in m and nearest.code in m for m in _texts(at.markdown))

    # те же три числа — на странице «Сводный бюджет» (из того же ввода)
    goto(at, "svodny")
    summary_metrics = {m.label: m.value for m in at.metric}
    assert summary_metrics["Минимум: себестоимость"] == metrics["Минимум: себестоимость"]
    assert summary_metrics["Коэффициент снижения"] == metrics["Коэффициент снижения"]
    assert any("Минимум не калиброван" in w for w in _texts(at.warning))
    goto(at, "minimum")

    at.button(key="save_cost_geology").click()
    at.run(timeout=30)
    assert any("Расчёт себестоимости сохранён" in s for s in _texts(at.success))
    version_id = at.session_state["staged_sessions"]["geology"].version_id
    inputs, summary, _ = SmetaStore().load_cost_calc_for_chain(version_id)
    assert inputs["brigades"] == 3 and summary["cost"] == pytest.approx(expected.cost)

    at2 = open_app("versii")
    _click(at2, "Открыть версию")
    assert at2.exception == []
    goto(at2, "minimum")
    assert at2.number_input(key="cost_brigades").value == 3
    assert at2.number_input(key="cost_salary_itr").value == 50000.0


# --- имя файла выгрузки: шифр объекта и дата (Э3/Э5, проверка 25.10.2026) ---


def test_export_file_names_use_object_code_and_calc_date(isolated_smeta_db, monkeypatch):
    import datetime

    import streamlit

    names = {}
    original = streamlit.download_button

    def recording(label, *args, **kwargs):
        names[label] = kwargs.get("file_name")
        return original(label, *args, **kwargs)

    monkeypatch.setattr(streamlit, "download_button", recording)
    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.text_input(key="obj_code").set_value("Т-01/ИГИ")
    at.run(timeout=30)
    goto(at, "vedomost")
    _geology_confirmed_estimate(at)
    goto(at, "svodny")
    assert at.exception == []
    assert names["Скачать Excel сметы"] == "Смета_geology_Т-01_ИГИ_2026-09-30.xlsx"
    assert names["Скачать сводный бюджет (Excel)"] == "Сводный_бюджет_Т-01_ИГИ_2026-09-30.xlsx"


# --- исправления по независимой проверке ветки (25.10.2026) ---


def test_confirmed_estimate_with_changed_distance_leaves_budget(isolated_smeta_db):
    at = open_app("obekt")
    at.number_input(key="obj_distance_km").set_value(22.0)
    at.run(timeout=30)
    goto(at, "vedomost")
    _geology_confirmed_estimate(at)
    goto(at, "svodny")
    assert list(_budget_table(at)["Раздел"]) == ["Геология (ОИГ)", "ИТОГО"]

    goto(at, "obekt")
    at.number_input(key="obj_distance_km").set_value(3.0)
    at.run(timeout=30)
    assert at.exception == []
    # на смете — расхождение со снимком, «готов для бюджета» нет
    goto(at, "smeta")
    assert any("Смета устарела и не входит в сводный бюджет" in w for w in _texts(at.warning))
    assert not any("готов для сводного бюджета" in s for s in _texts(at.success))
    # в сводном бюджете — модуль исключён с причиной
    goto(at, "svodny")
    assert any("Геология (ОИГ): подтверждённая смета не совпадает" in w for w in _texts(at.warning))
    assert not [d for d in at.dataframe if "Итого с НДС" in d.value.columns]


def test_cost_inputs_survive_module_switch(isolated_smeta_db):
    # и переход между страницами: ввод на «Минимальный бюджет», смена модуля
    # на «Ведомость объёмов», возврат
    at = open_app()
    _geology_to_estimate(at)
    goto(at, "minimum")
    at.number_input(key="cost_brigades").set_value(3)
    at.number_input(key="cost_salary_itr").set_value(50000.0)
    at.run(timeout=30)
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)
    at.radio(key="module_choice").set_value("geology")
    at.run(timeout=30)
    goto(at, "minimum")
    assert at.exception == []
    assert at.number_input(key="cost_brigades").value == 3
    assert at.number_input(key="cost_salary_itr").value == 50000.0


def test_opening_version_without_object_data_clears_fields(isolated_smeta_db):
    at = open_app()
    _geology_to_estimate(at)  # версия без данных объекта

    at2 = open_app("obekt")
    at2.text_input(key="obj_code").set_value("ДРУГОЙ")
    at2.number_input(key="obj_distance_km").set_value(22.0)
    at2.run(timeout=30)
    goto(at2, "versii")
    _click(at2, "Открыть версию")
    assert at2.exception == []
    goto(at2, "obekt")
    assert at2.text_input(key="obj_code").value == ""
    assert at2.number_input(key="obj_distance_km").value is None


# --- своя строка (Э7): работа вне сборника на странице сметы ---


def _ecology_to_estimate(at):
    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)
    at.number_input(key="field_ecology_radiation_area").set_value(22.5)
    at.number_input(key="field_ecology_observation_points").set_value(5)
    at.number_input(key="field_ecology_recon_length_km").set_value(4)
    at.run(timeout=30)
    at.button(key="confirm_ecology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    at.number_input(key="field_ecology_estimate_lab_total_base").set_value(13813)
    at.run(timeout=30)


def test_custom_line_added_on_estimate_page_goes_to_total_version_and_budget(isolated_smeta_db):
    import datetime

    at = open_app()
    at.text_input(key="save_smeta_name").set_value("Своя строка")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    _ecology_to_estimate(at)
    assert at.exception == []
    session = at.session_state["staged_sessions"]["ecology"]
    assert any(block.value == "**Свои строки (работы вне сборника)**" for block in at.markdown)

    # ключ формы добавления — с номером записи журнала (после добавления форма новая)
    prefix = f"custom_ecology_estimate_add_{len(session.overrides['estimate'].history())}"
    # без основания — ошибка, строка не добавлена
    at.text_input(key=f"{prefix}_name").set_value("Бактериологические исследования почвы")
    at.text_input(key=f"{prefix}_unit").set_value("проба")
    at.number_input(key=f"{prefix}_qty").set_value(10)
    at.number_input(key=f"{prefix}_price").set_value(4500)
    _click(at, "Добавить строку")
    assert at.exception == []
    assert any("не указано основание" in e for e in _texts(at.error))
    assert session.overrides["estimate"].custom_lines() == {}
    # ввод после ошибки не стёрт (ревью Э7, №4) — достаточно дописать основание
    assert at.text_input(key=f"{prefix}_name").value == "Бактериологические исследования почвы"

    at.text_input(key=f"{prefix}_basis").set_value("прайс ООО «лаборатория»")
    _click(at, "Добавить строку")
    assert at.exception == []
    lines = session.overrides["estimate"].custom_lines()
    assert [d["name"] for d in lines.values()] == ["Бактериологические исследования почвы"]
    assert any("Работы вне сборника после индекса" in m and money_text(45000) in m
               for m in _texts(at.markdown))
    # одна главная кнопка на странице — «Подтвердить смету»
    assert [b.label for b in at.button if b.proto.type == "primary"] == ["Подтвердить смету"]
    # в истории правок — запись своей строки
    assert any("своя строка" in str(row) for t in at.table for row in t.value.values.tolist())

    at.button(key="confirm_ecology_estimate").click()
    at.run(timeout=30)
    assert at.exception == []
    result = session.result("estimate")
    assert "custom.1" in result.lines

    # версия переоткрывается со своей строкой
    at2 = open_app("versii")
    _click(at2, "Открыть версию")
    goto(at2, "smeta")
    assert at2.exception == []
    reopened = at2.session_state["staged_sessions"]["ecology"]
    assert reopened.result("estimate").lines["custom.1"].basis == "По прайсу: прайс ООО «лаборатория»"
    assert reopened.last_stage_status() == STATUS_CONFIRMED

    # удаление — запись в журнале, строка уходит из сметы
    at2.button(key="custom_ecology_estimate_delete_custom.1").click()
    at2.run(timeout=30)
    assert at2.exception == []
    assert reopened.overrides["estimate"].custom_lines() == {}
    assert "custom.1" not in reopened.compute("estimate", at2.session_state["_run_context"].refs).effective
    # причина расхождения со снимком названа точно (ревью Э7, №2)
    assert any("после подтверждения внесены правки или свои строки" in w for w in _texts(at2.warning))


# --- Э9: снижение цены на странице «5. Сводный бюджет»; находка ревью Э7 ---


def _ecology_confirmed_with_custom_line(at, price=100000):
    import datetime

    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    _ecology_to_estimate(at)
    session = at.session_state["staged_sessions"]["ecology"]
    prefix = f"custom_ecology_estimate_add_{len(session.overrides['estimate'].history())}"
    at.text_input(key=f"{prefix}_name").set_value("Бактериология")
    at.number_input(key=f"{prefix}_qty").set_value(1)
    at.number_input(key=f"{prefix}_price").set_value(price)
    at.text_input(key=f"{prefix}_basis").set_value("прайс")
    _click(at, "Добавить строку")
    at.button(key="confirm_ecology_estimate").click()
    at.run(timeout=30)
    assert at.exception == []


def test_budget_table_shows_after_index_custom_lines(isolated_smeta_db):
    """Находка ревью Э7: «С индексацией» = База × Индекс + работы вне
    сборника после индекса — колонка и пояснение на странице."""
    from app.ui import BUDGET_AFTER_INDEX_COLUMN

    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    assert at.exception == []
    table = _budget_table(at)
    row = table.iloc[0]
    assert row[BUDGET_AFTER_INDEX_COLUMN] == 100000.0
    assert row["С индексацией"] == pytest.approx(
        row["База (в ценах раздела)"] * 81.95 + 100000.0, abs=1.0)  # база — с округлением до копейки
    assert any("работы вне сборника после индекса" in c for c in _texts(at.caption))


def test_budget_reductions_entered_on_page_go_to_total(isolated_smeta_db):
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    before = _budget_table(at)["Итого с НДС"].iloc[-1]
    indexed = _budget_table(at)["С индексацией"].iloc[0]

    at.number_input(key="obj_red_ecology").set_value(0.65)
    at.number_input(key="obj_red_budget").set_value(0.8)
    at.run(timeout=30)
    assert at.exception == []
    table = _budget_table(at)
    assert table["Коэф. раздела"].iloc[0] == 0.65
    assert table["Коэф. сводной"].iloc[-1] == 0.8
    # как в Excel: «после снижения раздела» — только раздел; отдельно — раздел × сводная
    assert table["После снижения раздела, без НДС"].iloc[0] == pytest.approx(indexed * 0.65, abs=0.02)
    assert table["После снижения раздела, без НДС"].iloc[-1] == pytest.approx(indexed * 0.65, abs=0.02)
    assert table["После снижения раздела и сводной, без НДС"].iloc[-1] == pytest.approx(
        indexed * 0.65 * 0.8, abs=0.02)
    assert table["Итого с НДС"].iloc[-1] == pytest.approx(before * 0.65 * 0.8, abs=0.02)
    assert at.session_state["_run_context"].object_data.price_reductions == {
        "ecology": 0.65, "budget": 0.8}

    button = next(b for b in at.get("download_button") if b.proto.label.startswith("Скачать сводный"))
    assert button.proto.disabled is False

    # к 1 — снова прежний итог (контрольные суммы не меняются по умолчанию)
    at.number_input(key="obj_red_ecology").set_value(1.0)
    at.number_input(key="obj_red_budget").set_value(1.0)
    at.run(timeout=30)
    assert _budget_table(at)["Итого с НДС"].iloc[-1] == pytest.approx(before, abs=0.005)
    assert at.session_state["_run_context"].object_data.price_reductions == {}


def test_budget_mobilization_display_keeps_total(isolated_smeta_db):
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    total = _budget_table(at)["Итого с НДС"].iloc[-1]
    at.checkbox(key="obj_show_mobilization").check()
    at.run(timeout=30)
    assert at.exception == []
    assert _budget_table(at)["Итого с НДС"].iloc[-1] == total
    assert any("В том числе мобилизация" in m for m in _texts(at.markdown))


# --- ревью сводного бюджета: снижение цены при открытии версии, ИТОГО ---------


def _set_version_object_data(db_path, data):
    """Подменить данные объекта у всех версий базы (имитация старой версии
    со своим снижением или с битым значением)."""
    import json
    import sqlite3

    con = sqlite3.connect(db_path)
    with con:
        con.execute("UPDATE smeta_versions SET object_data = ?", (json.dumps(data),))
    con.close()


def _reductions_entered(at):
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    at.number_input(key="obj_red_ecology").set_value(0.65)
    at.number_input(key="obj_red_budget").set_value(0.8)
    at.checkbox(key="obj_show_mobilization").check()
    at.run(timeout=30)
    assert at.exception == []
    return _budget_table(at)["Итого с НДС"].iloc[-1]


def _open_latest_version(at):
    """Открыть версию в той же вкладке: в ней уже есть расчёт раздела —
    первое нажатие просит подтвердить замену, второе открывает."""
    goto(at, "versii")
    _click(at, "Открыть версию")
    _click(at, "Открыть версию")
    assert at.exception == []


def test_opening_version_keeps_current_reductions_and_total(isolated_smeta_db):
    """Ревью сводной №1: ввёл снижение → открыл версию → снижение на месте,
    итог тот же (раньше открытие ставило 1,0 и итог молча рос)."""
    at = open_app()
    reduced = _reductions_entered(at)
    _open_latest_version(at)
    goto(at, "svodny")
    assert at.exception == []
    assert at.number_input(key="obj_red_ecology").value == 0.65
    assert at.number_input(key="obj_red_budget").value == 0.8
    assert at.checkbox(key="obj_show_mobilization").value is True
    assert at.session_state["_run_context"].object_data.price_reductions == {
        "ecology": 0.65, "budget": 0.8}
    assert _budget_table(at)["Итого с НДС"].iloc[-1] == pytest.approx(reduced, abs=0.005)
    # честная подпись: где хранятся коэффициенты
    assert any("Где хранятся коэффициенты" in c for c in _texts(at.caption))


def test_opening_version_with_own_reductions_reports_and_keeps_current(
    isolated_smeta_db, tmp_path
):
    at = open_app()
    reduced = _reductions_entered(at)
    _set_version_object_data(
        tmp_path / "test_smety.sqlite", {"price_reductions": {"ecology": 0.5}})
    _open_latest_version(at)
    warnings = _texts(at.warning)
    notice = next(w for w in warnings if "в открытой версии было снижение цены" in w)
    assert "(Экология ×0.5)" in notice
    assert "оставлено текущее (" in notice and "Экология ×0.65" in notice
    assert "Сводный бюджет ×0.8" in notice
    goto(at, "svodny")
    assert at.number_input(key="obj_red_ecology").value == 0.65
    assert _budget_table(at)["Итого с НДС"].iloc[-1] == pytest.approx(reduced, abs=0.005)


def test_opening_version_with_broken_reduction_value_does_not_fail(isolated_smeta_db, tmp_path):
    """Ревью сводной №3: нечисловое значение снижения в сохранённых данных
    объекта не роняет открытие версии — пропускается с предупреждением."""
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    _set_version_object_data(
        tmp_path / "test_smety.sqlite",
        {"code": "Т-1", "price_reductions": {"budget": "не число", "ecology": "0,8"}})
    at2 = open_app("versii")
    _click(at2, "Открыть версию")
    assert at2.exception == []
    assert any("нечисловое значение" in w and "не число" in w for w in _texts(at2.warning))
    goto(at2, "obekt")
    assert at2.text_input(key="obj_code").value == "Т-1"


def test_budget_total_row_sums_only_modules_in_total(isolated_smeta_db, monkeypatch):
    """Ревью сводной №4: модуль без итога с НДС (нет индекса) не входит в
    колонки строки ИТОГО — они сходятся с итогом."""
    import app.ui as ui
    from core.pricing import calculate_total_with_vat as real

    def no_index_for_geology(lines, refs, work_type, **kwargs):
        totals = real(lines, refs, work_type=work_type, **kwargs)
        if work_type == "geology":
            import dataclasses
            return dataclasses.replace(
                totals, indexed_total=None, vat_amount=None, total_with_vat=None,
                warnings=["индекс не найден (тест)"])
        return totals

    monkeypatch.setattr(ui, "calculate_total_with_vat", no_index_for_geology)
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("geology")
    at.run(timeout=30)
    _geology_to_estimate(at)
    at.button(key="confirm_geology_estimate").click()
    at.run(timeout=30)
    goto(at, "svodny")
    assert at.exception == []
    table = _budget_table(at)
    total = table.iloc[-1]
    ecology = table[table["Раздел"] == "Экология (ИЭИ)"].iloc[0]
    assert total["Раздел"] == "ИТОГО (по вошедшим в итог)"
    assert total["С индексацией"] == pytest.approx(ecology["С индексацией"], abs=0.005)
    assert total[ui.BUDGET_AFTER_INDEX_COLUMN] == pytest.approx(
        ecology[ui.BUDGET_AFTER_INDEX_COLUMN], abs=0.005)
    assert total["Итого с НДС"] == pytest.approx(ecology["Итого с НДС"], abs=0.005)


# --- демо-база: «Открыть все разделы расчёта» (страница «Версии») ---


@pytest.fixture(scope="module")
def demo_db_file(tmp_path_factory):
    from core.references import DEFAULT_DB_PATH
    from tools import make_demo

    if not (make_demo.ROAD_XLSX.exists() and make_demo.KARIER_XLSX.exists()):
        pytest.skip("нет эталона в reference_data/")
    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    path = tmp_path_factory.mktemp("demo") / "smety.sqlite"
    make_demo.build_demo(path)
    return path


@pytest.fixture
def demo_db(demo_db_file, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", demo_db_file)
    return demo_db_file


def _open_calculation(at, name):
    goto(at, "versii")
    at.selectbox(key="open_calculation_choice").set_value(name)
    at.run(timeout=30)
    _click(at, "Открыть все разделы расчёта")
    assert at.exception == []
    goto(at, "svodny")  # переход st.switch_page AppTest не выполняет сам
    assert at.exception == []
    return _budget_table(at)


def test_open_all_sections_of_demo_calculation_goes_to_budget(demo_db):
    from tools import make_demo

    rows, _obj, errors = make_demo.read_back(demo_db, make_demo.ROAD_NAME)
    assert errors == []
    at = open_app("versii")
    names = at.selectbox(key="open_calculation_choice").options
    assert any(make_demo.ROAD_NAME in o for o in names) and any(make_demo.KARIER_NAME in o for o in names)
    table = _open_calculation(at, make_demo.ROAD_NAME)
    sections = list(table["Раздел"])[:-1]
    assert len(sections) == 5
    assert table["Итого с НДС"].iloc[-1] == pytest.approx(
        sum(r.total_with_vat for r in rows), abs=0.05)
    assert at.session_state["calc_date"].isoformat() == make_demo.DATE
    assert at.session_state["_run_context"].object_data.code == "Т-01"


def test_open_second_calculation_asks_and_replaces_all_sections(demo_db):
    """Во вкладке уже открыт расчёт — первое нажатие предупреждает, второе
    открывает; раздел, которого во втором расчёте нет (геофизика у Карьера),
    из вкладки убирается — в сводную не попадает."""
    from tools import make_demo

    at = open_app("versii")
    _open_calculation(at, make_demo.ROAD_NAME)
    goto(at, "versii")
    at.selectbox(key="open_calculation_choice").set_value(make_demo.KARIER_NAME)
    at.run(timeout=30)
    _click(at, "Открыть все разделы расчёта")
    assert any("уже есть ввод разделов" in w for w in _texts(at.warning))
    table = _open_calculation(at, make_demo.KARIER_NAME)
    rows, _obj, _errors = make_demo.read_back(demo_db, make_demo.KARIER_NAME)
    assert len(table) == 4 + 1 and not any("Геофизика" in r for r in table["Раздел"])
    assert table["Итого с НДС"].iloc[-1] == pytest.approx(
        sum(r.total_with_vat for r in rows), abs=0.05)
    assert at.session_state["_run_context"].object_data.code == "Т-02"
