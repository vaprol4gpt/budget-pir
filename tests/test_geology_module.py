import pytest

from core.models import calculate_amount
from modules.geology.module import (
    MONOLITY_DISPERSNYE_PER_HOLE,
    MONOLITY_SKALNYE_PER_HOLE,
    PROBY_VODY_PER_HOLE,
    TERMOMETRIYA_RATE_PER_HOLE,
    GeologyModule,
)


class FakeReferenceBook:
    """Двойник ReferenceBook для теста стаба без обращения к SQLite."""

    def __init__(self, prices, categories=None):
        self._prices = prices
        self._categories = categories or {}

    def get_price(self, source, table, item, category=None):
        key = (source, table, item, category)
        if key not in self._prices:
            raise KeyError(key)
        return self._prices[key]

    def get_categories(self, source, table, item):
        return self._categories.get((source, table, item), [])


@pytest.fixture
def refs():
    """Гипотетический случай "всё есть в справочнике" — включает все
    коды расценок, задействованные в build_estimate (в реальном
    справочнике табл.57 п.2 отсутствует, см. `refs_realistic` ниже и
    записи разработки)."""
    return FakeReferenceBook(
        {
            ("СБЦ-99", "17", "1", "IV кат"): (45.6, "Таблица 17, п.1 (бурение)"),
            ("СБЦ-99", "4", "3", None): (0.0875, "Таблица 4, п.3 (транспорт)"),
            ("СБЦ-99", "93", "3", "точек"): (14.4, "Таблица 93, п.3 (привязка)"),
            ("СБЦ-99", "82", "1", None): (8.2, "Таблица 82, п.1 (камеральная бурения)"),
            ("СБЦ-99", "81", "2", None): (500.0, "Таблица 81, п.2 (программа работ)"),
            ("СБЦ-99", "87", "2", None): (0.22, "Таблица 87, п.2 (техотчёт)"),
            ("СБЦ-99", "40", "3", None): (34.6, "Таблица 40, п.3 (термометрия)"),
            ("СБЦ-99", "57", "1", None): (22.9, "Таблица 57, п.1 (монолиты дисперсных)"),
            ("СБЦ-99", "57", "2", None): (30.6, "Таблица 57, п.2 (монолиты скальных)"),
            ("СБЦ-99", "60", "2", None): (7.6, "Таблица 60, п.2 (пробы воды)"),
            ("СБЦ-99", "64", "2", None): (4.8, "Таблица 64, п.2 (сумм. влажность)"),
            ("СБЦ-99", "73", "2", None): (67.3, "Таблица 73, п.2 (анализ воды)"),
            ("СБЦ-99", "68", "3", None): (122.2, "Таблица 68, п.3 (скальных физ-мех)"),
            ("СБЦ-99", "86", "5", None): (0.15, "Таблица 86, п.5 (камеральная хим.воды)"),
            ("СБЦ-99", "86", "2", None): (0.15, "Таблица 86, п.2 (камеральная песчаных)"),
        }
    )


@pytest.fixture
def refs_realistic():
    """Копия реального состояния справочника на сегодня: табл.57 п.2
    (монолиты скальных грунтов) отсутствует — исключён как
    конфликтующий ключ ещё раньше (см. записи разработки)."""
    return FakeReferenceBook(
        {
            ("СБЦ-99", "17", "1", "IV кат"): (45.6, "Таблица 17, п.1 (бурение)"),
            ("СБЦ-99", "4", "3", None): (0.0875, "Таблица 4, п.3 (транспорт)"),
            ("СБЦ-99", "93", "3", "точек"): (14.4, "Таблица 93, п.3 (привязка)"),
            ("СБЦ-99", "82", "1", None): (8.2, "Таблица 82, п.1 (камеральная бурения)"),
            ("СБЦ-99", "81", "2", None): (500.0, "Таблица 81, п.2 (программа работ)"),
            ("СБЦ-99", "87", "2", None): (0.22, "Таблица 87, п.2 (техотчёт)"),
            ("СБЦ-99", "40", "3", None): (34.6, "Таблица 40, п.3 (термометрия)"),
            ("СБЦ-99", "57", "1", None): (22.9, "Таблица 57, п.1 (монолиты дисперсных)"),
            # табл.57 п.2 намеренно отсутствует (как в справочнике до Э6а —
            # проверка, что строка без цены не роняет расчёт)
            ("СБЦ-99", "60", "2", None): (7.6, "Таблица 60, п.2 (пробы воды)"),
            ("СБЦ-99", "64", "2", None): (4.8, "Таблица 64, п.2 (сумм. влажность)"),
            ("СБЦ-99", "73", "2", None): (67.3, "Таблица 73, п.2 (анализ воды)"),
            ("СБЦ-99", "68", "3", None): (122.2, "Таблица 68, п.3 (скальных физ-мех)"),
            ("СБЦ-99", "86", "5", None): (0.15, "Таблица 86, п.5 (камеральная хим.воды)"),
            ("СБЦ-99", "86", "2", None): (0.15, "Таблица 86, п.2 (камеральная песчаных)"),
        }
    )


# --- build_volumes: класс А (жёсткая связь с числом скважин/метражом) ---


def test_build_volumes_class_a_geodesy_privyazka_equals_drilling_count(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["geodesy_privyazka"] == 30


def test_build_volumes_class_a_kameral_bureniya_equals_drilling_length(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["kameral_bureniya"] == 450


def test_build_volumes_class_a_programma_and_tehotchet_always_one(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["programma_rabot"] == 1
    assert volumes["tehotchet"] == 1


def test_build_volumes_uses_operator_input_for_drilling_line(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "VII", "drilling_length": 175}], "drilling_count": 12}, refs
    )
    assert volumes["drilling_catVII"] == 175


# --- build_volumes: класс Б (устойчивый коэффициент от числа скважин) ---


def test_build_volumes_class_b_termometriya_is_half_of_drilling_count(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["termometriya"] == pytest.approx(TERMOMETRIYA_RATE_PER_HOLE * 30)
    assert volumes["termometriya"] == pytest.approx(15.0)


# --- build_volumes: класс В (типовой профиль — середина диапазона на скважину) ---


def test_build_volumes_class_v_uses_midpoint_of_range_per_hole(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["monolity_dispersnye"] == pytest.approx(MONOLITY_DISPERSNYE_PER_HOLE * 30)
    assert volumes["monolity_skalnye"] == pytest.approx(MONOLITY_SKALNYE_PER_HOLE * 30)
    assert volumes["proby_vody"] == pytest.approx(PROBY_VODY_PER_HOLE * 30)
    # значения середины диапазона зафиксированы явно, чтобы падать при
    # случайном изменении констант в docs/predmetnaya_oblast.md
    assert MONOLITY_DISPERSNYE_PER_HOLE == pytest.approx(2.48)
    assert MONOLITY_SKALNYE_PER_HOLE == pytest.approx(1.1975)
    assert PROBY_VODY_PER_HOLE == pytest.approx(0.1695)


def test_build_volumes_zero_drilling_count_gives_zero_class_b_and_v_volumes(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 0}, refs
    )
    assert volumes["termometriya"] == 0
    assert volumes["monolity_dispersnye"] == 0
    assert volumes["monolity_skalnye"] == 0
    assert volumes["proby_vody"] == 0


# --- build_estimate: бурение и транспорт (без учёта новых позиций — см. Stage 2) ---


def test_build_estimate_uses_real_prices_from_refs(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 300}], "drilling_count": 20}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    drilling = estimate["geology.drilling.IV"]
    assert drilling.price == pytest.approx(45.6)
    assert drilling.qty == 300
    assert calculate_amount(drilling, estimate) == pytest.approx(300 * 45.6)

    transport = estimate["geology.transport"]
    assert transport.coefs["rate"] == pytest.approx(0.0875)
    # с Э6а база — все полевые строки (лист 1.2.1_ИГИ!D151), не только бурение
    field = sum(calculate_amount(estimate[i], estimate) for i in transport.base_ids)
    assert "geology.drilling.IV" in transport.base_ids
    assert calculate_amount(transport, estimate) == pytest.approx(field * 0.0875)
    assert field > 300 * 45.6


# --- комбинированный грунт: несколько категорий на одном объекте ---


def test_build_volumes_combined_categories_keeps_separate_lengths_per_category(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {
            "drilling_by_category": [
                {"soil_category": "II", "drilling_length": 172},
                {"soil_category": "IV", "drilling_length": 602},
                {"soil_category": "VI", "drilling_length": 602},
                {"soil_category": "VIII", "drilling_length": 345},
            ],
            "drilling_count": 30,
        },
        refs,
    )
    assert volumes["drilling_catII"] == 172
    assert volumes["drilling_catIV"] == 602
    assert volumes["drilling_catVI"] == 602
    assert volumes["drilling_catVIII"] == 345


def test_build_volumes_combined_categories_kameral_bureniya_uses_total_length(refs):
    # позиции, зависящие от ОБЩЕГО метража (камеральная обработка
    # бурения), должны считаться от суммы по всем категориям — 172+602
    # +602+345=1721, как в реальной ведомости объёмов заказчика
    # (Приложение №3 к Бюджету контракта объекта А)
    module = GeologyModule()
    volumes = module.build_volumes(
        {
            "drilling_by_category": [
                {"soil_category": "II", "drilling_length": 172},
                {"soil_category": "IV", "drilling_length": 602},
                {"soil_category": "VI", "drilling_length": 602},
                {"soil_category": "VIII", "drilling_length": 345},
            ],
            "drilling_count": 30,
        },
        refs,
    )
    assert volumes["kameral_bureniya"] == 1721


def test_build_estimate_combined_categories_creates_one_line_per_category():
    refs_combined = FakeReferenceBook(
        {
            ("СБЦ-99", "17", "1", "II кат"): (38.4, "Таблица 17, п.1, II кат"),
            ("СБЦ-99", "17", "1", "IV кат"): (45.6, "Таблица 17, п.1, IV кат"),
            ("СБЦ-99", "4", "3", None): (0.0875, "Таблица 4, п.3 (транспорт)"),
        }
    )
    module = GeologyModule()
    volumes = module.build_volumes(
        {
            "drilling_by_category": [
                {"soil_category": "II", "drilling_length": 172},
                {"soil_category": "IV", "drilling_length": 602},
            ],
            "drilling_count": 30,
        },
        refs_combined,
    )
    estimate = module.build_estimate(volumes, refs_combined)

    drilling_ii = estimate["geology.drilling.II"]
    drilling_iv = estimate["geology.drilling.IV"]
    assert drilling_ii.qty == 172
    assert drilling_ii.price == pytest.approx(38.4)
    assert drilling_iv.qty == 602
    assert drilling_iv.price == pytest.approx(45.6)

    # цена у категорий РАЗНАЯ — расчёт по одной "средней" категории на
    # весь объём был бы системно неверным (см. modules/geology/module.py)
    assert drilling_ii.price != drilling_iv.price


def test_build_estimate_combined_categories_transport_sums_all_drilling_lines():
    refs_combined = FakeReferenceBook(
        {
            ("СБЦ-99", "17", "1", "II кат"): (38.4, "..."),
            ("СБЦ-99", "17", "1", "IV кат"): (45.6, "..."),
            ("СБЦ-99", "4", "3", None): (0.0875, "..."),
        }
    )
    module = GeologyModule()
    volumes = module.build_volumes(
        {
            "drilling_by_category": [
                {"soil_category": "II", "drilling_length": 172},
                {"soil_category": "IV", "drilling_length": 602},
            ],
            "drilling_count": 30,
        },
        refs_combined,
    )
    estimate = module.build_estimate(volumes, refs_combined)

    transport = estimate["geology.transport"]
    # обе строки бурения в базе (плюс остальные полевые — с Э6а)
    assert {"geology.drilling.II", "geology.drilling.IV"} <= set(transport.base_ids)
    assert set(transport.base_ids) == {
        "geology.drilling.II", "geology.drilling.IV", "geology.geodesy_privyazka",
        "geology.termometriya", "geology.monolity_dispersnye", "geology.monolity_skalnye",
        "geology.proby_vody",
    }


def test_build_estimate_missing_category_does_not_raise(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "II", "drilling_length": 200}], "drilling_count": 15}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    drilling = estimate["geology.drilling.II"]
    assert drilling.price == 0.0
    assert drilling.qty == 200  # объём сохраняется, даже если цена не найдена
    assert "II" in drilling.basis
    assert "не найдена в справочнике" in drilling.basis


def test_check_returns_warning_when_price_missing(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "II", "drilling_length": 200}], "drilling_count": 15}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    missing_price_warnings = [w for w in warnings if "не найдена в справочнике" in w]
    assert len(missing_price_warnings) == 1
    assert "II" in missing_price_warnings[0]


def test_check_has_no_missing_price_warnings_when_all_prices_found(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 300}], "drilling_count": 20}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    assert not any("не найдена в справочнике" in w for w in warnings)


# --- build_estimate: новые позиции классов А/Б/В (Stage 2) ---


def test_build_estimate_creates_all_class_a_lines_with_prices(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    privyazka = estimate["geology.geodesy_privyazka"]
    assert privyazka.qty == 30
    assert privyazka.price == pytest.approx(14.4)
    assert calculate_amount(privyazka, estimate) == pytest.approx(30 * 14.4)

    kameral = estimate["geology.kameral_bureniya"]
    assert kameral.qty == 450
    assert kameral.price == pytest.approx(8.2)

    programma = estimate["geology.programma_rabot"]
    assert programma.qty == 1
    assert programma.price == pytest.approx(500.0)


def test_build_estimate_tehotchet_is_percentage_of_kameralnyi_blok_not_constant(refs):
    # Регрессия: раньше техотчёт считался как qty=1 × price=0.22 =
    # 0.22 руб. — неверно, в эталоне 0.22 это СТАВКА от суммы
    # камеральных строк (16950x0,22=3729 в исходной смете), а не
    # готовая цена за единицу. См. записи разработки.
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    tehotchet = estimate["geology.tehotchet"]
    assert tehotchet.kind == "B"
    assert tehotchet.coefs["rate"] == pytest.approx(0.22)
    # с Э6а — все камеральные строки (лист 1.2.1_ИГИ!D148 = H146)
    assert set(tehotchet.base_ids) == {
        "geology.programma_rabot", "geology.kameral_bureniya",
        "geology.kameralnaya_himsostav_vody", "geology.kameral_lab_peschanye",
    }

    kameralnyi_blok = sum(calculate_amount(estimate[i], estimate) for i in tehotchet.base_ids)
    expected = kameralnyi_blok * 0.22
    assert calculate_amount(tehotchet, estimate) == pytest.approx(expected)
    # с текущими объёмами (30 скважин, 450 п.м.) сумма НЕ должна
    # оставаться константой 0.22 — она должна расти вместе с объёмами
    assert calculate_amount(tehotchet, estimate) > 1.0


def test_build_estimate_creates_class_b_termometriya_line(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    termometriya = estimate["geology.termometriya"]
    assert termometriya.qty == pytest.approx(15.0)  # 0.5 * 30
    assert termometriya.price == pytest.approx(34.6)


def test_build_estimate_creates_all_class_v_lines(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    dispersnye = estimate["geology.monolity_dispersnye"]
    assert dispersnye.qty == pytest.approx(MONOLITY_DISPERSNYE_PER_HOLE * 30)
    assert dispersnye.price == pytest.approx(22.9)

    skalnye = estimate["geology.monolity_skalnye"]
    assert skalnye.qty == pytest.approx(MONOLITY_SKALNYE_PER_HOLE * 30)
    assert skalnye.price == pytest.approx(30.6)

    voda = estimate["geology.proby_vody"]
    assert voda.qty == pytest.approx(PROBY_VODY_PER_HOLE * 30)
    assert voda.price == pytest.approx(7.6)


def test_build_estimate_handles_realistic_missing_price_without_raising(refs_realistic):
    # табл.57 п.2 реально отсутствует в справочнике (см. фикстуру) —
    # расчёт не должен упасть на этом
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs_realistic
    )

    estimate = module.build_estimate(volumes, refs_realistic)

    skalnye = estimate["geology.monolity_skalnye"]
    assert skalnye.price == 0.0
    assert skalnye.qty == pytest.approx(MONOLITY_SKALNYE_PER_HOLE * 30)  # объём не теряется
    assert "не найдена в справочнике" in skalnye.basis
    assert "монолитов скальных" in skalnye.basis

    warnings = module.check(estimate)
    assert any("не найдена в справочнике" in w and "скальных" in w for w in warnings)


# --- check(): предупреждения о типовом профиле класса В (Stage 3) ---


def test_check_warns_about_all_three_class_v_lines_by_default(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)
    profile_warnings = [w for w in warnings if "типовому профилю" in w]

    assert len(profile_warnings) == 3
    joined = " ".join(profile_warnings)
    assert "дисперсных" in joined
    assert "скальных" in joined
    assert "проб воды" in joined or "Отбор проб воды" in joined


def test_check_class_v_warning_mentions_it_is_not_operator_data(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)
    profile_warnings = [w for w in warnings if "типовому профилю" in w]

    # оператор должен видеть, что это НЕ введённые им данные
    assert all("не по данным оператора" in w for w in profile_warnings)


def test_check_class_v_and_missing_price_warnings_coexist(refs_realistic):
    # монолиты скальных — одновременно и класс В (типовой профиль), и
    # реально отсутствующая цена: должны появиться оба предупреждения
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs_realistic
    )
    estimate = module.build_estimate(volumes, refs_realistic)

    warnings = module.check(estimate)

    assert any("типовому профилю" in w and "скальных" in w for w in warnings)
    assert any("не найдена в справочнике" in w and "скальных" in w for w in warnings)


# --- build_volumes: лабораторные позиции (Блок 1, записи разработки) ---


def test_build_volumes_lab_summarnaya_vlazhnost_equals_monolity_dispersnye(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["lab_summarnaya_vlazhnost"] == pytest.approx(volumes["monolity_dispersnye"])


def test_build_volumes_lab_analiz_vody_equals_proby_vody(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["lab_analiz_vody"] == pytest.approx(volumes["proby_vody"])


def test_build_volumes_lab_skalnye_fizmeh_equals_monolity_skalnye(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["lab_skalnye_fizmeh"] == pytest.approx(volumes["monolity_skalnye"])


# --- build_estimate: лабораторные строки получают цену ---


def test_build_estimate_creates_all_three_lab_lines_with_prices(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    vlazhnost = estimate["geology.lab_summarnaya_vlazhnost"]
    assert vlazhnost.qty == pytest.approx(volumes["monolity_dispersnye"])
    assert vlazhnost.price == pytest.approx(4.8)

    analiz_vody = estimate["geology.lab_analiz_vody"]
    assert analiz_vody.qty == pytest.approx(volumes["proby_vody"])
    assert analiz_vody.price == pytest.approx(67.3)

    skalnye_fizmeh = estimate["geology.lab_skalnye_fizmeh"]
    assert skalnye_fizmeh.qty == pytest.approx(volumes["monolity_skalnye"])
    assert skalnye_fizmeh.price == pytest.approx(122.2)


# --- check(): лабораторный профиль — отдельная, более осторожная пометка ---


def test_check_warns_about_lab_profile_lines_with_distinct_wording(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)
    lab_warnings = [w for w in warnings if "лабораторный профиль" in w]

    assert len(lab_warnings) == 3
    # формулировка обязана отличаться от предупреждения класса В
    assert not any("типовому профилю" in w for w in lab_warnings)
    joined = " ".join(lab_warnings)
    assert "одного объекта" in joined


def test_check_class_v_and_lab_profile_warnings_use_different_wording(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    class_v_warnings = {w for w in warnings if "типовому профилю" in w}
    lab_warnings = {w for w in warnings if "лабораторный профиль" in w}
    assert class_v_warnings and lab_warnings
    assert class_v_warnings.isdisjoint(lab_warnings)


# --- Блок 2: камеральная обработка хим.состава воды ---


def test_build_estimate_kameralnaya_vody_is_percentage_of_analiz_vody_only(refs):
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )

    estimate = module.build_estimate(volumes, refs)

    kameralnaya_vody = estimate["geology.kameralnaya_himsostav_vody"]
    assert kameralnaya_vody.kind == "B"
    assert kameralnaya_vody.coefs["rate"] == pytest.approx(0.15)
    assert kameralnaya_vody.base_ids == ["geology.lab_analiz_vody"]

    analiz_vody_amount = calculate_amount(estimate["geology.lab_analiz_vody"], estimate)
    expected = analiz_vody_amount * 0.15
    assert calculate_amount(kameralnaya_vody, estimate) == pytest.approx(expected)


def test_build_estimate_kameralnaya_vody_sums_only_referenced_line_not_whole_estimate(refs):
    # Самое важное для этого блока: base_ids должен суммировать РОВНО
    # указанную строку(и), а не всю смету и не весь лабораторный блок.
    # Проверяем это, сравнивая с заведомо БОЛЬШИМИ альтернативными
    # суммами (весь лабораторный блок, вся смета) — если бы
    # calculate_amount ошибочно просуммировал их, тест бы это поймал.
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    kameralnaya_vody = estimate["geology.kameralnaya_himsostav_vody"]
    actual = calculate_amount(kameralnaya_vody, estimate)

    analiz_vody_amount = calculate_amount(estimate["geology.lab_analiz_vody"], estimate)
    correct_expected = analiz_vody_amount * 0.15
    assert actual == pytest.approx(correct_expected)

    # Альтернатива А: если бы по ошибке суммировали ВСЕ лабораторные
    # строки (а не только анализ воды) — сумма была бы другой.
    all_lab_amount = sum(
        calculate_amount(estimate[line_id], estimate)
        for line_id in (
            "geology.lab_summarnaya_vlazhnost",
            "geology.lab_analiz_vody",
            "geology.lab_skalnye_fizmeh",
        )
    )
    wrong_if_whole_lab_block = all_lab_amount * 0.15
    assert actual != pytest.approx(wrong_if_whole_lab_block)

    # Альтернатива Б: если бы по ошибке суммировали вообще все строки
    # сметы (base_ids проигнорирован) — сумма была бы совсем другой
    # (на порядки больше).
    whole_estimate_amount = sum(
        calculate_amount(line, estimate)
        for line_id, line in estimate.items()
        if line_id != kameralnaya_vody.id
    )
    wrong_if_whole_estimate = whole_estimate_amount * 0.15
    assert actual != pytest.approx(wrong_if_whole_estimate)


def test_build_estimate_kameralnaya_vody_handles_missing_rate_gracefully():
    # Если табл.86 п.5 вдруг отсутствует в справочнике — не падаем,
    # как и для остальных позиций (единый механизм _get_price_or_missing).
    module = GeologyModule()
    prices_without_kameralnaya_vody = {
        ("СБЦ-99", "17", "1", "IV кат"): (45.6, "..."),
        ("СБЦ-99", "4", "3", None): (0.0875, "..."),
        ("СБЦ-99", "93", "3", "точек"): (14.4, "..."),
        ("СБЦ-99", "82", "1", None): (8.2, "..."),
        ("СБЦ-99", "81", "2", None): (500.0, "..."),
        ("СБЦ-99", "87", "2", None): (0.22, "..."),
        ("СБЦ-99", "40", "3", None): (34.6, "..."),
        ("СБЦ-99", "57", "1", None): (22.9, "..."),
        ("СБЦ-99", "57", "2", None): (30.6, "..."),
        ("СБЦ-99", "60", "2", None): (7.6, "..."),
        ("СБЦ-99", "64", "2", None): (4.8, "..."),
        ("СБЦ-99", "73", "2", None): (67.3, "..."),
        ("СБЦ-99", "68", "3", None): (122.2, "..."),
        # табл.86 п.5 намеренно отсутствует
    }
    refs_no_kameralnaya_vody = FakeReferenceBook(prices_without_kameralnaya_vody)
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30},
        refs_no_kameralnaya_vody,
    )

    estimate = module.build_estimate(volumes, refs_no_kameralnaya_vody)

    kameralnaya_vody = estimate["geology.kameralnaya_himsostav_vody"]
    assert kameralnaya_vody.coefs["rate"] == 0.0
    assert "не найдена в справочнике" in kameralnaya_vody.basis
    assert calculate_amount(kameralnaya_vody, estimate) == 0.0

    warnings = module.check(estimate)
    assert any("не найдена в справочнике" in w and "хим.состава воды" in w for w in warnings)


# --- input_schema/field_options: автокомплит категории грунта ---


def test_input_schema_marks_soil_category_as_dynamic():
    module = GeologyModule()
    fields = {field.name: field for field in module.input_schema()}

    assert fields["drilling_by_category"].type == "group"
    sub_fields = {sub.name: sub for sub in fields["drilling_by_category"].group_fields}
    assert sub_fields["soil_category"].dynamic_options is True
    assert sub_fields["drilling_length"].dynamic_options is False
    assert fields["drilling_count"].dynamic_options is False


def test_field_options_comes_from_reference_book_not_hardcoded():
    # ключевая проверка: варианты — ровно то, что вернул refs, а не
    # захардкоженный в коде список категорий
    refs_with_categories = FakeReferenceBook(
        {},
        categories={
            ("СБЦ-99", "17", "1"): [
                ("I кат", 36.0),
                ("II кат", 38.4),
                ("III кат", 42.6),
            ]
        },
    )
    module = GeologyModule()

    options = module.field_options("soil_category", refs_with_categories)

    assert options == [
        ("I", "I — 36 руб/п.м."),
        ("II", "II — 38.4 руб/п.м."),
        ("III", "III — 42.6 руб/п.м."),
    ]


def test_field_options_uses_different_categories_when_reference_book_differs():
    # тот же тест, но с ДРУГИМ набором категорий — если бы список был
    # захардкожен в коде, результат не изменился бы вместе со справочником
    refs_with_different_categories = FakeReferenceBook(
        {},
        categories={("СБЦ-99", "17", "1"): [("V кат", 99.9)]},
    )
    module = GeologyModule()

    options = module.field_options("soil_category", refs_with_different_categories)

    assert options == [("V", "V — 99.9 руб/п.м.")]


def test_field_options_excludes_non_standard_category_strings():
    # "IV кат при глубине скв. до10м" — не проходит по шаблону "<римские
    # цифры> кат" (см. записи разработки, шаг 2) и не должна попасть в список
    refs_with_special_category = FakeReferenceBook(
        {},
        categories={
            ("СБЦ-99", "17", "1"): [
                ("IV кат", 45.6),
                ("IV кат при глубине скв. до10м", 45.6),
            ]
        },
    )
    module = GeologyModule()

    options = module.field_options("soil_category", refs_with_special_category)

    assert options == [("IV", "IV — 45.6 руб/п.м.")]


def test_field_options_returns_empty_list_when_reference_book_has_no_categories():
    refs_empty = FakeReferenceBook({}, categories={})
    module = GeologyModule()

    assert module.field_options("soil_category", refs_empty) == []


def test_field_options_returns_empty_list_for_unrelated_field_name():
    refs_with_categories = FakeReferenceBook(
        {}, categories={("СБЦ-99", "17", "1"): [("I кат", 36.0)]}
    )
    module = GeologyModule()

    assert module.field_options("drilling_length", refs_with_categories) == []


def test_build_volumes_receives_clean_category_code_not_display_string(refs):
    # то самое разведение "что показывается / что передаётся": в
    # build_volumes должен попасть код "IV", а не подпись с ценой
    module = GeologyModule()

    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}, refs
    )

    assert "drilling_catIV" in volumes
    assert not any("руб" in key for key in volumes)  # ни один ключ не содержит подпись


def test_build_estimate_does_not_break_when_soil_category_is_none(refs):
    # так придёт из UI, если справочник пуст и оператору нечего выбрать
    # (см. app/streamlit_app.py) — build_volumes/build_estimate не должны
    # падать, просто не найдут цену (как для любой неизвестной категории,
    # см. test_build_estimate_missing_category_does_not_raise выше)
    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": None, "drilling_length": 450}], "drilling_count": 30}, refs
    )
    assert volumes["drilling_catNone"] == 450

    estimate = module.build_estimate(volumes, refs)

    drilling = estimate["geology.drilling.None"]
    assert drilling.price == 0.0
    assert "не найдена в справочнике" in drilling.basis


# --- Э6а (30.10.2026): позиции листа 1.2.1_ИГИ, коэффициенты, базы ---

from modules.geology.module import COEFFICIENT_FIELDS, OPTIONAL_VOLUME_KEYS  # noqa: E402

BASE_INPUT = {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}], "drilling_count": 30}


def test_new_positions_absent_when_not_entered(refs):
    module = GeologyModule()
    sheet = module.build_volume_sheet(BASE_INPUT)
    assert not OPTIONAL_VOLUME_KEYS & set(sheet)
    estimate = module.build_estimate(module.build_volumes(BASE_INPUT, refs), refs)
    assert not {f"geology.{k}" for k in OPTIONAL_VOLUME_KEYS} & set(estimate)


def test_new_positions_appear_from_input_with_derived_office_lines():
    sheet = GeologyModule().build_volume_sheet(
        {**BASE_INPUT, "rekognoscirovka_km": 15, "gidronablyudeniya_length": 150, "shtamp_count": 9}
    )
    assert sheet["rekognoscirovka"].qty == 15
    assert sheet["kameral_rekognoscirovki"].qty == 15
    assert sheet["shtamp"].qty == 9
    # камералка бурения: т.82 п.1 — метраж без гидронаблюдений, п.2 — с ними
    assert sheet["kameral_bureniya"].qty == 300
    assert sheet["kameral_bureniya_gidro"].qty == 150


def test_coefficients_default_neutral_and_applied_when_entered(refs):
    module = GeologyModule()
    volumes = module.build_volumes(BASE_INPUT, refs)
    plain = module.build_estimate(volumes, refs)
    assert plain["geology.programma_rabot"].coefs == {}
    k = module.build_estimate(volumes, refs, parameters={"k_programma_rabot": 1.25, "k_monolity_skalnye": 1})
    assert k["geology.programma_rabot"].coefs == {"k": 1.25}
    assert calculate_amount(k["geology.programma_rabot"], k) == pytest.approx(625)
    assert "т.81 прим.1" in k["geology.programma_rabot"].basis
    assert k["geology.monolity_skalnye"].coefs == {}
    assert set(COEFFICIENT_FIELDS) == {"k_programma_rabot", "k_monolity_skalnye", "k_rekognoscirovka"}


def test_kameral_lab_lines_follow_sheet_bases(refs):
    module = GeologyModule()
    volumes = module.build_volumes(BASE_INPUT, refs)
    estimate = module.build_estimate(volumes, refs)
    # без новых лабораторных: п.2 (песчаные) — от суммарной влажности и скальных,
    # п.1 и п.8 — нет базы, строк нет
    assert estimate["geology.kameral_lab_peschanye"].base_ids == [
        "geology.lab_summarnaya_vlazhnost", "geology.lab_skalnye_fizmeh",
    ]
    assert "geology.kameral_lab_glinistye" not in estimate
    assert "geology.kameral_korroziya" not in estimate


# --- Ревью модуля геологии (30.10.2026): находки 1–6 ---

from core.validation import below_min_fields  # noqa: E402
from modules.geology.module import coefficient_warnings  # noqa: E402


def test_unapplied_sheet_coefficients_warned_only_for_present_lines(refs):
    module = GeologyModule()
    estimate = module.build_estimate(module.build_volumes(BASE_INPUT, refs), refs)
    warnings = module.check_estimate(estimate)
    text = [w for w in warnings if "Коэффициенты листа-эталона" in w]
    assert len(text) == 1
    assert "1,25 (E128)" in text[0] and "0,7 (E42)" in text[0]
    # рекогносцировки в смете нет — о её коэффициенте молчим
    assert "E20" not in text[0]
    assert "условия вашего объекта те же" in text[0]


def test_sheet_coefficient_warning_disappears_when_applied(refs):
    module = GeologyModule()
    data = {**BASE_INPUT, "rekognoscirovka_km": 15}
    volumes = module.build_volumes(data, refs)
    params = {"k_programma_rabot": 1.25, "k_monolity_skalnye": 0.7}
    warnings = module.check_estimate(module.build_estimate(volumes, refs, parameters=params))
    text = [w for w in warnings if "Коэффициенты листа-эталона" in w]
    assert len(text) == 1 and "1,2 (E20)" in text[0] and "E128" not in text[0]
    params["k_rekognoscirovka"] = 1.2
    warnings = module.check_estimate(module.build_estimate(volumes, refs, parameters=params))
    assert not [w for w in warnings if "Коэффициенты листа-эталона" in w]


def test_coefficient_defaults_stay_neutral():
    fields = {f.name: f for f in GeologyModule().input_schema()}
    for name in COEFFICIENT_FIELDS:
        assert fields[name].default == 1.0


def test_zero_coefficient_rejected_by_schema_and_warned(refs):
    module = GeologyModule()
    schema = module.input_schema()
    labels = below_min_fields(schema, {"k_monolity_skalnye": 0})
    assert labels == [COEFFICIENT_FIELDS["k_monolity_skalnye"][1]]
    assert not below_min_fields(schema, {"k_monolity_skalnye": 0.7})
    estimate = module.build_estimate(
        module.build_volumes(BASE_INPUT, refs), refs, parameters={"k_monolity_skalnye": 0}
    )
    assert any("коэффициент 0 — строка обнуляется" in w for w in coefficient_warnings(estimate))


def test_rekognoscirovka_item_marked_as_assumption(refs):
    module = GeologyModule()
    data = {**BASE_INPUT, "rekognoscirovka_km": 15}
    sheet = module.build_volume_sheet(data)
    assert "ДОПУЩЕНИЕ" in sheet["rekognoscirovka"].basis
    estimate = module.build_estimate(module.build_volumes(data, refs), refs)
    assert "т.9 п.3 — по объекту эталона" in estimate["geology.rekognoscirovka"].basis
    assert "т.9 п.3 — по объекту эталона" in estimate["geology.kameral_rekognoscirovki"].basis
    assert any("т.9 п.3" in w and "ДОПУЩЕНИЕ" in w for w in module.check_estimate(estimate))
    # без рекогносцировки предупреждения о пункте нет
    plain = module.build_estimate(module.build_volumes(BASE_INPUT, refs), refs)
    assert not any("т.9 п.3" in w for w in module.check_estimate(plain))


def test_kameral_bureniya_rule_marked_and_gidro_over_drilling_warned():
    module = GeologyModule()
    sheet = module.build_volume_sheet({**BASE_INPUT, "gidronablyudeniya_length": 150})
    assert "ДОПУЩЕНИЕ" in sheet["kameral_bureniya"].basis
    assert "D132" in sheet["kameral_bureniya"].basis
    assert not any("больше всего метража" in w for w in module.check_volume_sheet(sheet))
    over = module.build_volume_sheet({**BASE_INPUT, "gidronablyudeniya_length": 600})
    assert over["kameral_bureniya"].qty == 0
    assert any("больше всего метража" in w for w in module.check_volume_sheet(over))


def test_kameral_lab_p2_shows_base_and_skips_zero_base(refs):
    module = GeologyModule()
    estimate = module.build_estimate(module.build_volumes(BASE_INPUT, refs), refs)
    line = estimate["geology.kameral_lab_peschanye"]
    assert "суммарная влажность" in line.name and "скальных" in line.name
    assert "D137" in line.basis and "суммарная влажность" in line.basis
    # строки базы правкой обнулены — базы нет, строки нет
    sheet = module.build_volume_sheet(
        BASE_INPUT, pinned={"monolity_dispersnye": {"qty": 0}, "monolity_skalnye": {"qty": 0}}
    )
    volumes = {k: v.qty for k, v in sheet.items() if k != "drilling_count"}
    assert "geology.kameral_lab_peschanye" not in module.build_estimate(volumes, refs)


def test_sbor_materialov_label_explains_unit():
    fields = {f.name: f for f in GeologyModule().input_schema()}
    assert "т.78" in fields["sbor_materialov_count"].label
    assert "не сверен" in fields["sbor_materialov_count"].label
