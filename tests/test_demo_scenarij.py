"""Демо-сценарий для защиты (материалы ВКР, 03.11.2026) — прогон через
интерфейс (AppTest), шаг за шагом, как его показывают на защите:

1. «1. Объект и ТЗ»: обезличенный объект «Т-01», дата расчёта 30.09.2026 (индекс
   III кв. 2026 есть в справочнике), параметры ТЗ → «Предложить ведомость»;
2. «2. Ведомость объёмов»: черновик из ТЗ, дописать категории грунта (геология),
   подтвердить; то же — геодезия и гидрометеорология;
3. «3. Максимальный бюджет»: смета, правила параметров, «итог содержит N допущений»,
   подтвердить;
4. «4. Минимальный бюджет»: себестоимость геологии (ставки — условные числа и
   seed-допущения, плашка «не калиброван»);
5. «5. Сводный бюджет»: итог, снижение вводом коэффициента сводной, Excel — файл
   открывается openpyxl, в нём тот же итог, что на экране.

Числа ниже — расчёт программы на ТЕСТОВЫХ объёмах (не данные заказчика); если они
изменились — меняется расчёт, и документ сценария надо обновить вместе с тестом.
Страницу 5 параллельно дорабатывают (целевая цена, лист КП) — тест завязан только
на итог (таблица разделов), поле коэффициента сводной и Excel-файл сводного бюджета.

Сценарий не ходит в сеть: соединения с внешними адресами в тесте запрещены."""

import datetime
import io
import re
import socket
from pathlib import Path

import openpyxl
import pytest

import core.persistence as persistence_module
import run_budget_stub
from core.stages import STATUS_CONFIRMED
from tests.test_streamlit_app import goto, open_app

CALC_DATE = datetime.date(2026, 9, 30)

# Шаг 1 — данные объекта и параметры ТЗ (условные, объект «Т-01»).
OBJECT = {"obj_code": "Т-01", "obj_name": "Учебная автодорога"}
DISTANCE_KM = 22.0
TZ = {
    "length_km": 10.0, "survey_section_m": 0.5,
    "hole_spacing_m": 250.0, "hole_depth_m": 10.0, "strip_width_m": 100.0,
}
SECTIONS = ("geology", "geodesy", "hydromet")

# Шаг 2 — что оператор дописывает к черновику из ТЗ.
GEOLOGY_CATEGORIES = [("IV", 250.0), ("V", 150.0)]   # 400 п.м. из ТЗ, разнесены по категориям
GEODESY_INPUT = {"oporny_points_count": 4, "oporny_kameral_points_count": 4}
HYDROMET_INPUT = {k: v for k, v in run_budget_stub.HYDROMET_VOLUMES.items() if k != "recon_route_km"}
HYDROMET_PARAMETERS = run_budget_stub.HYDROMET_PARAMETERS

# Шаг 4 — ввод себестоимости геологии (условные числа, НЕ ставки компании).
COST_INPUT = dict(
    brigades=3, itr=2, drivers=2, cooks=1, salary_worker=50000.0, salary_itr=50000.0,
    salary_driver=50000.0, salary_cook=50000.0, fuel_price_diesel=70.0, lab_samples=10,
)
BUDGET_REDUCTION = 0.9

# Ожидаемые числа (руб.; расчёт программы на объёмах выше, дата 30.09.2026).
EXPECTED = {
    "geology": {"total": "4 155 130,73", "assumptions": 3},
    "geodesy": {"total": "4 794 524,84", "assumptions": 6},
    "hydromet": {"total": "1 565 371,20", "assumptions": 5},
}
OBJECT_ASSUMPTIONS = 1                     # допущения уровня данных объекта
EXPECTED_TOTAL_ASSUMPTIONS = sum(e["assumptions"] for e in EXPECTED.values()) + OBJECT_ASSUMPTIONS
EXPECTED_GEOLOGY_MAX = "3 405 844,86"      # смета геологии без НДС
EXPECTED_GEOLOGY_MIN = "3 059 048,58"      # себестоимость геологии
EXPECTED_GEOLOGY_COEF = "0,898"            # минимум / максимум
EXPECTED_TOTAL = 10_515_026.77             # итого с НДС, без снижения
EXPECTED_TOTAL_NO_VAT = 8_618_874.40
EXPECTED_REDUCED_TOTAL = 9_463_524.09      # итого с НДС при снижении сводной ×0,9
EXPECTED_REDUCED_NO_VAT = 7_756_986.96


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


@pytest.fixture
def no_network(monkeypatch):
    """Сценарий должен проходить без интернета: любое соединение не с этим ПК — ошибка."""
    original = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"демо-сценарий обратился в сеть: {address}")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def captured_downloads(monkeypatch):
    """Данные кнопок «Скачать …» по ключу виджета (AppTest файл не отдаёт)."""
    import streamlit

    files = {}
    original = streamlit.download_button

    def recording(label, *args, **kwargs):
        files[kwargs.get("key") or label] = kwargs.get("data")
        return original(label, *args, **kwargs)

    monkeypatch.setattr(streamlit, "download_button", recording)
    return files


def _texts(elements):
    """Тексты элементов; неразрывные пробелы разрядов (money_text) — обычные."""
    return [str(element.value).replace(" ", " ") for element in elements]


def _set(at, key, value):
    for widget in at.number_input:
        if widget.key == key:
            widget.set_value(value)
            return
    at.selectbox(key=key).set_value(value)


def _section_total_text(at):
    """«Итого с индексацией … и НДС …: **N руб.**» страницы сметы."""
    return next(m for m in _texts(at.markdown) if m.startswith("Итого с индексацией"))


def _budget_table(at):
    return next(d.value for d in at.dataframe if "Итого с НДС" in d.value.columns)


def _confirm_estimate(at, code):
    """Шаг 3 для раздела: сумма, предупреждение о допущениях, подтверждение."""
    assert "3. Максимальный бюджет (смета по сборникам)" in _texts(at.header)
    goto(at, "smeta")
    assert at.exception == []
    expected = EXPECTED[code]
    assert f"{expected['total']} руб." in _section_total_text(at)
    n = expected["assumptions"]
    assert any(w.startswith(f"Итог раздела содержит {n} допущ") for w in _texts(at.warning))
    # под параметром с правилом — подпись правила (статус, значение, эталон)
    assert any(c.startswith("Правило:") for c in _texts(at.caption))
    at.button(key=f"confirm_{code}_estimate").click()
    at.run(timeout=30)
    assert at.exception == []
    assert at.session_state["staged_sessions"][code].last_stage_status() == STATUS_CONFIRMED


def test_demo_scenario(isolated_smeta_db, no_network, captured_downloads):
    # --- шаг 1: «1. Объект и ТЗ» --------------------------------------------------
    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(CALC_DATE)
    for key, value in OBJECT.items():
        at.text_input(key=key).set_value(value)
    at.number_input(key="obj_distance_km").set_value(DISTANCE_KM)
    at.selectbox(key="obj_tz_object_kind").set_value("linear")
    at.selectbox(key="obj_tz_survey_scale").set_value("1:1000")
    at.selectbox(key="obj_tz_permafrost").set_value("yes")
    for name, value in TZ.items():
        at.number_input(key=f"obj_tz_{name}").set_value(value)
    assert at.button(key="tz_propose").disabled  # разделы ещё не отмечены
    for code in SECTIONS:
        at.checkbox(key=f"obj_tz_sec_{code}").check()
    at.run(timeout=30)
    assert at.exception == []
    assert not at.button(key="tz_propose").disabled
    at.button(key="tz_propose").click()
    at.run(timeout=30)

    # --- шаг 2: «2. Ведомость объёмов», геология ---------------------------------
    goto(at, "vedomost")
    assert at.exception == []
    assert at.radio(key="module_choice").value == "geology"
    assert any("предложены по параметрам ТЗ" in i for i in _texts(at.info))  # блок «Черновик из ТЗ»
    # из ТЗ: 10 км / шаг 250 м = 40 скважин; × 10 м = 400 п.м.; рекогносцировка 10 км
    assert at.number_input(key="field_geology_drilling_count").value == 40
    assert at.number_input(key="group_geology_drilling_by_category_0_drilling_length").value == 400
    provenance = " ".join(str(df.value.to_dict()) for df in at.dataframe)
    assert "правило (жёсткая связь)" in provenance and "оценка — проверьте" in provenance
    # ТЗ не даёт категорий грунта — оператор разносит метраж по категориям
    at.button(key="add_geology_drilling_by_category").click()
    at.run(timeout=30)
    for row, (category, meters) in enumerate(GEOLOGY_CATEGORIES):
        at.selectbox(key=f"group_geology_drilling_by_category_{row}_soil_category").set_value(category)
        at.number_input(key=f"group_geology_drilling_by_category_{row}_drilling_length").set_value(meters)
    at.run(timeout=30)
    assert at.exception == []
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)

    # --- шаг 3: «3. Максимальный бюджет», геология --------------------------------
    _confirm_estimate(at, "geology")

    # --- шаги 2–3: геодезия -------------------------------------------------------
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("geodesy")
    at.run(timeout=30)
    # из ТЗ: съёмка 1:1000, сечение 0,5 м, 10 км × 100 м = 100 га; трасса 10 км
    assert at.number_input(key="group_geodesy_topo_surveys_0_topo_area").value == 100
    assert at.number_input(key="field_geodesy_trassa_km").value == 10
    for name, value in GEODESY_INPUT.items():
        _set(at, f"field_geodesy_{name}", value)
    at.run(timeout=30)
    at.button(key="confirm_geodesy_volumes").click()
    at.run(timeout=30)
    _confirm_estimate(at, "geodesy")

    # --- шаги 2–3: гидрометеорология ------------------------------------------------
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("hydromet")
    at.run(timeout=30)
    assert at.number_input(key="field_hydromet_recon_route_km").value == 10
    for name, value in HYDROMET_INPUT.items():
        _set(at, f"field_hydromet_{name}", value)
    at.run(timeout=30)
    at.button(key="confirm_hydromet_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    # у ИГМИ параметры сметы без правила — ввод оператора; пока не заполнены, итога нет
    assert any("обязательные поля" in i for i in _texts(at.info))
    for name, value in HYDROMET_PARAMETERS.items():
        _set(at, f"field_hydromet_estimate_{name}", value)
    at.run(timeout=30)
    _confirm_estimate(at, "hydromet")

    # --- шаг 4: «4. Минимальный бюджет» (себестоимость геологии) -------------------
    goto(at, "minimum")
    # раздел страницы 4 — из боковой панели (с 03.11.2026 минимум есть у всех разделов)
    at.radio(key="module_choice").set_value("geology")
    at.run(timeout=30)
    for name, value in COST_INPUT.items():
        at.number_input(key=f"cost_{name}").set_value(value)
    at.run(timeout=30)
    assert at.exception == []
    metrics = {m.label: str(m.value).replace(" ", " ") for m in at.metric}
    assert metrics["Максимум: смета по СБЦ (без НДС)"] == EXPECTED_GEOLOGY_MAX
    assert metrics["Минимум: себестоимость"] == EXPECTED_GEOLOGY_MIN
    assert metrics["Коэффициент снижения"] == EXPECTED_GEOLOGY_COEF
    assert any(w.startswith("Минимум не калиброван") for w in _texts(at.warning))

    # --- шаг 5: «5. Сводный бюджет» ---------------------------------------------------
    goto(at, "svodny")
    assert at.exception == []
    table = _budget_table(at)
    assert list(table["Раздел"]) == [
        "Геология (ОИГ)", "Геодезия (ИГДИ)", "Гидрометеорология (ИГМИ)", "ИТОГО"]
    assert table["Итого с НДС"].iloc[-1] == pytest.approx(EXPECTED_TOTAL, abs=0.005)
    assert table["После снижения раздела и сводной, без НДС"].iloc[-1] == pytest.approx(
        EXPECTED_TOTAL_NO_VAT, abs=0.005)
    assert any(f"Итог содержит {EXPECTED_TOTAL_ASSUMPTIONS} допущений" in w for w in _texts(at.warning))

    # снижение сводной ×0,9: в публичной копии не ниже коэффициента геологии (0,898) — без предупреждения
    at.number_input(key="obj_red_budget").set_value(BUDGET_REDUCTION)
    at.run(timeout=30)
    assert at.exception == []
    table = _budget_table(at)
    total = table["Итого с НДС"].iloc[-1]
    assert total == pytest.approx(EXPECTED_REDUCED_TOTAL, abs=0.005)
    assert total == pytest.approx(EXPECTED_TOTAL * BUDGET_REDUCTION, abs=0.02)
    assert not any("ниже минимума" in w for w in _texts(at.warning))

    # Excel сводного бюджета: открывается openpyxl, итог = на экране
    data = captured_downloads.get("download_budget")
    assert data, "кнопка «Скачать сводный бюджет (Excel)» не отдала файл"
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    numbers = [
        cell for sheet in workbook for row in sheet.iter_rows(values_only=True)
        for cell in row if isinstance(cell, (int, float))
    ]
    assert any(abs(n - total) < 0.005 for n in numbers)
    assert any(abs(n - EXPECTED_REDUCED_NO_VAT) < 0.005 for n in numbers)
    info = {row[0]: row[1] for row in workbook["Сведения"].iter_rows(values_only=True) if row}
    assert info["Дата расчёта"] == "30.09.2026"
    assert info["Шифр объекта"] == "Т-01"


DEMO_DOC = Path(__file__).resolve().parent.parent / "docs" / "diplom" / "demo_scenarij.md"


def test_demo_doc_assumption_counts():
    """Числа допущений в документе сценария = числам, которые проверяет прогон выше."""
    if not DEMO_DOC.exists():
        pytest.skip("материалы ВКР не публикуются")
    text = " ".join(DEMO_DOC.read_text(encoding="utf-8").split())
    sections = [int(n) for n in re.findall(r"Итог раздела содержит (\d+) допущ", text)]
    assert sections == [EXPECTED[code]["assumptions"] for code in SECTIONS]
    totals = [int(n) for n in re.findall(r"Итог содержит (\d+) допущ", text)]
    assert totals == [EXPECTED_TOTAL_ASSUMPTIONS]
    breakdown = re.search(r"Итог содержит \d+ допущений в параметрах смет: ([^»]*)»", text).group(1)
    parts = dict(item.rsplit(" — ", 1) for item in breakdown.split("; "))
    assert [int(parts[label]) for label in ("Геология (ОИГ)", "Геодезия (ИГДИ)",
                                            "Гидрометеорология (ИГМИ)")] == [
        EXPECTED[code]["assumptions"] for code in SECTIONS]
    assert int(parts["данные объекта"]) == OBJECT_ASSUMPTIONS
