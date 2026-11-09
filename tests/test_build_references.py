import sqlite3

import openpyxl
import pytest

from data.build_references import (
    IndexEntry,
    PriceEntry,
    build_database,
    extract_ecology_index_entries,
    extract_ecology_price_entries,
    extract_geodesy_index_entries,
    extract_geodesy_price_entries,
    extract_geophysics_index_entries,
    extract_geophysics_price_entries,
    extract_index_entries,
    extract_price_entries,
    parse_basis,
    parse_ecology_basis,
    parse_quarterly_index,
    primary_basis_segment,
    quarter_period,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("СБЦ-99 Таблица 93, п.3", ("СБЦ-99", "93", "3")),
        ("Таблица 9, п.3", (None, "9", "3")),
        ("Таблица 11, п.1", (None, "11", "1")),
        ("т.267 п.3", (None, "267", "3")),
        ("СЦиР-82 табл.410, п.18", ("СЦиР-82", "410", "18")),
        ("С.ц.1982г.                      т267 п.6", ("С.ц.1982г", "267", "6")),
        ("Таблица 3 п.9 и п.8е ОУ", (None, None, None)),
        ("п. 13 ОУ", (None, None, None)),
        (" Таблица 4, п.3", (None, "4", "3")),
    ],
)
def test_parse_basis(text, expected):
    assert parse_basis(text) == expected


def _write_row(ws, row, a=None, b=None, c=None, d=None, e=None, f=None):
    ws.cell(row=row, column=1, value=a)
    ws.cell(row=row, column=2, value=b)
    ws.cell(row=row, column=3, value=c)
    ws.cell(row=row, column=4, value=d)
    ws.cell(row=row, column=5, value=e)
    ws.cell(row=row, column=6, value=f)


def test_extract_price_entries_simple_row():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(
        ws, 1, a=1, b="Плановая привязка скважин", d=100, e=14.4,
        f="СБЦ-99 Таблица 93, п.3",
    )

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=1)

    assert skipped == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.source == "СБЦ-99"
    assert entry.table == "93"
    assert entry.item == "3"
    assert entry.price == 14.4
    assert entry.category is None
    assert entry.name == "Плановая привязка скважин"


def test_extract_price_entries_skips_section_header():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(ws, 1, a="I. Геодезические работы")

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == []


def test_extract_price_entries_skips_coefficient_rows():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(ws, 1, a=2, b="Рекогносцировка", d=15, e=47.2, f="СБЦ-99 Таблица 9, п.3")
    _write_row(ws, 2, c="к=", e=1.2, f="прим. 3")

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=2)

    assert len(entries) == 1
    assert skipped == []


def test_extract_price_entries_flags_missing_basis_as_skipped():
    wb = openpyxl.Workbook()
    ws = wb.active
    # строка-продолжение блока категорий: нет своего основания (F пусто)
    _write_row(ws, 1, c="II кат", d=0, e=38.4)

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_price_entries_flags_ambiguous_basis_as_skipped():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(
        ws, 1, a=33, b="Районный коэффициент", d=1000, e=0.5,
        f="Таблица 3 п.9 и п.8е ОУ",
    )

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_price_entries_forward_fills_basis_for_category_block():
    # воспроизводит строки 26-29 исходного листа: категория I бурения
    # со своим основанием, дальше категории II-IV без своего основания
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(
        ws, 1, a=3, b="Колонковое бурение скважин диам. до 160 мм.",
        c="I кат", d=0, e=36, f="Таблица 17, п.1",
    )
    _write_row(ws, 2, c="II кат", d=0, e=38.4)
    _write_row(ws, 3, c="III кат", d=0, e=42.6)
    _write_row(ws, 4, c="IV кат", d=300, e=45.6)

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=4)

    assert skipped == []
    assert len(entries) == 4
    by_category = {e.category: e for e in entries}
    assert by_category["I кат"].table == "17" and by_category["I кат"].item == "1"
    assert by_category["II кат"].table == "17" and by_category["II кат"].item == "1"
    assert by_category["II кат"].price == pytest.approx(38.4)
    assert by_category["IV кат"].price == pytest.approx(45.6)
    # унаследованное наименование тоже протягивается (уже была такая логика)
    assert by_category["IV кат"].name == "Колонковое бурение скважин диам. до 160 мм."


def test_extract_price_entries_forward_fill_resets_on_section_header():
    # категория без собственного основания, но ПЕРЕД ней — заголовок
    # раздела, а не строка с явным основанием: наследовать нечего
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(ws, 1, a="III. Буровые работы")
    _write_row(ws, 2, c="II кат", d=0, e=38.4)

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=2)

    assert entries == []
    assert skipped == [2]


def test_extract_price_entries_does_not_forward_fill_rows_without_category():
    # строка-продолжение БЕЗ категории (просто число в E, как строки
    # 71/73/75/77/79 исходного листа) не должна наследовать основание —
    # иначе ей приписалась бы цена совсем другой работы
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(
        ws, 1, a=11, b="Содержание базы экспедиции", d=2, e=5700,
        f="СБЦ Таблица 101, п.5",
    )
    _write_row(ws, 2, e=1)  # безымянный коэффициент, без категории и без F

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=2)

    assert len(entries) == 1
    assert skipped == [2]


def test_extract_price_entries_forward_fill_survives_subtotal_row():
    # "Итого п.п. ..." — просто подытог, не должен сбрасывать протянутое
    # основание (в отличие от заголовка раздела)
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(
        ws, 1, a=3, b="Колонковое бурение", c="I кат", d=0, e=36,
        f="Таблица 17, п.1",
    )
    _write_row(ws, 2, a="Итого п. п. 1")
    _write_row(ws, 3, c="II кат", d=0, e=38.4)

    entries, skipped = extract_price_entries(ws, first_row=1, last_row=3)

    assert skipped == []
    assert len(entries) == 2


def test_extract_price_entries_keeps_category_when_present():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(
        ws, 1, a=3, b="Колонковое бурение скважин", c="I кат", d=0, e=36,
        f="Таблица 17, п.1",
    )

    entries, _ = extract_price_entries(ws, first_row=1, last_row=1)

    assert entries[0].category == "I кат"


def test_build_database_writes_rows_and_detects_conflicts(tmp_path):
    entries = [
        PriceEntry(
            source="СБЦ-99", table="9", item="3", category=None,
            price=47.2, basis="СБЦ-99 Таблица 9, п.3", name="Рекогносцировка", row=19,
        ),
        PriceEntry(
            source="СБЦ-99", table="17", item="1", category="I кат",
            price=36.0, basis="Таблица 17, п.1", name="Бурение", row=26,
        ),
        PriceEntry(
            source="СБЦ-99", table="9", item="3", category=None,
            price=999.0, basis="СБЦ-99 Таблица 9, п.3", name="Рекогносцировка", row=999,
        ),
    ]
    db_path = tmp_path / "test.sqlite"

    conflicts = build_database(entries, [], db_path)

    assert len(conflicts) == 1
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute('SELECT source, "table", item, category, price FROM prices').fetchall()
    finally:
        conn.close()
    # конфликтующий ключ (9, 3, None) исключён из справочника целиком —
    # остаётся только однозначная позиция (17, 1, "I кат")
    assert len(rows) == 1
    assert rows[0][4] == pytest.approx(36.0)


@pytest.mark.parametrize(
    "text, expected_value, expected_quarter, expected_year",
    [
        (
            "Итого с учетом коэффициента инфляции К-81,95 на III квартал "
            "2026 года (Письмо Минстроя России № 45358-ИФ/09 от 23.07.2026 г.)",
            81.95, "III", 2026,
        ),
        ("Итого с учетом коэффициента инфляции К-1,5 на I квартал 2027 года", 1.5, "I", 2027),
    ],
)
def test_parse_quarterly_index(text, expected_value, expected_quarter, expected_year):
    value, quarter, year = parse_quarterly_index(text)
    assert value == pytest.approx(expected_value)
    assert quarter == expected_quarter
    assert year == expected_year


@pytest.mark.parametrize(
    "text",
    [
        "Кроме того НДС, 20 %",  # нет индекса вовсе
        "Итого с учетом коэффициента инфляции К-81,95",  # нет квартала/года
        "",
    ],
)
def test_parse_quarterly_index_unrecognized_returns_none(text):
    assert parse_quarterly_index(text) == (None, None, None)


@pytest.mark.parametrize(
    "quarter, year, expected_start, expected_end",
    [
        ("I", 2026, "2026-01-01", "2026-03-31"),
        ("II", 2026, "2026-04-01", "2026-06-30"),
        ("III", 2026, "2026-07-01", "2026-09-30"),
        ("IV", 2026, "2026-10-01", "2026-12-31"),
        # 2028 — високосный год, февраль 29 дней
        ("I", 2028, "2028-01-01", "2028-03-31"),
    ],
)
def test_quarter_period(quarter, year, expected_start, expected_end):
    assert quarter_period(quarter, year) == (expected_start, expected_end)


def test_extract_index_entries_from_real_looking_row():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(
        row=1, column=1,
        value=(
            "Итого с учетом коэффициента инфляции К-81,95 на III квартал "
            "2026 года (Письмо Минстроя России № 45358-ИФ/09 от 23.07.2026 г.)"
        ),
    )

    entries, warnings = extract_index_entries(ws, row=1, work_type="geology")

    assert warnings == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.work_type == "geology"
    assert entry.value == pytest.approx(81.95)
    assert entry.period_start == "2026-07-01"
    assert entry.period_end == "2026-09-30"


def test_extract_index_entries_empty_row_gives_warning_not_crash():
    wb = openpyxl.Workbook()
    ws = wb.active

    entries, warnings = extract_index_entries(ws, row=1, work_type="geology")

    assert entries == []
    assert len(warnings) == 1


def test_extract_index_entries_unparseable_text_gives_warning_not_crash():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=1, column=1, value="Что-то непонятное без индекса")

    entries, warnings = extract_index_entries(ws, row=1, work_type="geology")

    assert entries == []
    assert len(warnings) == 1


# --- геофизика (Этап 6): primary_basis_segment / extract_geophysics_price_entries ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("т.267 п.3", "т.267 п.3"),
        (
            "т.267 п.3                                        \nК=1.2 с.252 п.6  \nК=1.15 т.265 п.2 ",
            "т.267 п.3",
        ),
        ("т.4 п.1                           ", "т.4 п.1"),
        (" К=0.05 П-22", ""),
    ],
)
def test_primary_basis_segment(text, expected):
    assert primary_basis_segment(text) == expected


def _write_geophysics_row(ws, row, a=None, b=None, c=None, d=None, e=None, f=None, g=None):
    ws.cell(row=row, column=1, value=a)
    ws.cell(row=row, column=2, value=b)
    ws.cell(row=row, column=3, value=c)
    ws.cell(row=row, column=4, value=d)
    ws.cell(row=row, column=5, value=e)
    ws.cell(row=row, column=6, value=f)
    ws.cell(row=row, column=7, value=g)


def test_extract_geophysics_price_entries_field_work_row_uses_price_column():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(
        ws, 1, a=1,
        b="Вертикальные электрические зондирования...",
        c="т.267 п.3                    \nК=1.2 с.252 п.6  \nК=1.15 т.265 п.2 \nК=1.1 т.265 п.9",
        d="ф.т", e=80, f=9, g=1.2,
    )

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=1)

    assert skipped == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.table == "267"
    assert entry.item == "3"
    assert entry.category is None
    assert entry.price == pytest.approx(9.0)
    assert entry.source == "СЦиР-1982"


def test_extract_geophysics_price_entries_percent_row_with_volume_uses_price_column():
    # unit='%' НО объём (E) заполнен -> ставка в F, как у обычной строки
    # (строка "Составление технического отчета" исходного листа)
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(
        ws, 1, a=15, b="Составление технического отчета",
        c="т.294 п.10                        К=1.2 т.294, прим.3",
        d="%", e=1000, f=0.1, g=None,
    )

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=1)

    assert skipped == []
    assert entries[0].price == pytest.approx(0.1)


def test_extract_geophysics_price_entries_percent_row_without_volume_uses_rate_column():
    # unit='%' И объём (E) пуст -> "цена" (F) на самом деле уже готовая
    # рублёвая база конкретной сметы, а не переиспользуемая ставка;
    # реальная ставка — в первом столбце коэффициентов (G). Строка
    # "Расходы по внутреннему транспорту" исходного листа.
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(
        ws, 1, a=17, b="Расходы по внутреннему транспорту, % от стоимости полевых работ",
        c="т.4 п.1                           ",
        d="%", e=None, f=14532.96, g=0.05,
    )

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=1)

    assert skipped == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.table == "4"
    assert entry.item == "1"
    assert entry.price == pytest.approx(0.05)


def test_extract_geophysics_price_entries_skips_section_and_subtotal_rows():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(ws, 1, a="Полевые работы:")
    _write_geophysics_row(ws, 2, a="Итого  полевых работ")

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=2)

    assert entries == []
    assert skipped == []  # не пронумерованные строки просто игнорируются, не считаются пропуском


def test_extract_geophysics_price_entries_skips_ambiguous_basis():
    # ссылка на страницу ("стр.288"), а не на таблицу — как у "Камеральная
    # обработка данных электроразведки" исходного листа
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(
        ws, 1, a=11, b="Камеральная обработка данных электроразведки",
        c="стр.288                                              К=1.15 П-22",
        d="%", e=1092.96, f=0.3, g=1.15,
    )

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_geophysics_price_entries_skips_multi_item_basis():
    # "т.3 п.3, п.7е ОУ" — ссылается сразу на два пункта, как у
    # "Районный коэффициент" исходного листа
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(
        ws, 1, a=21, b="Районный коэффициент",
        c="т.3 п.3, \nп.7е ОУ   \nП-22",
        d="%", e=None, f=24850.38, g=0.66,
    )

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_geophysics_price_entries_skips_row_missing_item_number():
    # "т.5 п." — без номера пункта, как у "Расходы по внешнему транспорту"
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(
        ws, 1, a=18, b="Расходы по внешнему транспорту",
        c="т.5 п.", d="%", e=None, f=15259.6, g=0.065,
    )

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_geophysics_price_entries_two_rows_same_code_no_conflict(tmp_path):
    # МПВ продольных и поперечных волн ссылаются на один и тот же код
    # расценки с одинаковой ценой — не должно быть конфликта при записи в БД
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geophysics_row(
        ws, 1, a=2, b="Сейсморазведка МПВ с поверхности, (продольные волны)",
        c="т.258 п.83                    К=1.2 с.252 п.6", d="ф.н.", e=400, f=14, g=1.2,
    )
    _write_geophysics_row(
        ws, 2, a=3, b="Сейсморазведка МПВ с поверхности, (поперечные волны)",
        c="т.258 п.83                    К=1.2 с.252 п.6", d="ф.н.", e=400, f=14, g=1.2,
    )

    entries, skipped = extract_geophysics_price_entries(ws, first_row=1, last_row=2)

    assert skipped == []
    assert len(entries) == 2
    conflicts = build_database(entries, [], tmp_path / "test.sqlite")
    assert conflicts == []


# --- индекс пересчёта цен геофизики (переиспользует период геологии) ---


def test_extract_geophysics_index_entries_reads_value_from_value_column():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=34, column=1, value=23)
    ws.cell(
        row=34, column=3,
        value=(
            "ВСЕГО инженерно-геофизческих работ с инфляционным индексом "
            "2 квартал 2025 г. Письмо Минстроя России №45358-ИФ/09 от 23.07.2026 г."
        ),
    )
    ws.cell(row=34, column=7, value=81.95)

    entries, warnings = extract_geophysics_index_entries(
        ws, geology_period=("2026-07-01", "2026-09-30"), row=34
    )

    assert warnings == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.work_type == "geophysics"
    assert entry.value == pytest.approx(81.95)
    # период геологии переиспользован буквально, а НЕ "2 квартал 2025"
    # из текста комментария этой строки (см. докстринг у
    # GEOPHYSICS_INDEX_ROW в data/build_references.py)
    assert entry.period_start == "2026-07-01"
    assert entry.period_end == "2026-09-30"


def test_extract_geophysics_index_entries_empty_cell_gives_warning_not_crash():
    wb = openpyxl.Workbook()
    ws = wb.active

    entries, warnings = extract_geophysics_index_entries(
        ws, geology_period=("2026-07-01", "2026-09-30"), row=34
    )

    assert entries == []
    assert len(warnings) == 1


def test_extract_geophysics_index_entries_non_numeric_value_gives_warning_not_crash():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=34, column=7, value="не число")

    entries, warnings = extract_geophysics_index_entries(
        ws, geology_period=("2026-07-01", "2026-09-30"), row=34
    )

    assert entries == []
    assert len(warnings) == 1


def test_build_database_writes_index_entries(tmp_path):
    index_entries = [
        IndexEntry(
            work_type="geology", period_start="2026-07-01", period_end="2026-09-30",
            value=81.95, basis="К-81,95 на III квартал 2026 года",
        ),
    ]
    db_path = tmp_path / "test.sqlite"

    build_database([], index_entries, db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT work_type, period_start, period_end, value FROM price_indices"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("geology", "2026-07-01", "2026-09-30", 81.95)]


# --- геодезия (Этап 7): extract_geodesy_price_entries / extract_geodesy_index_entries ---


def _write_geodesy_row(ws, row, a=None, b=None, c=None, d=None, e=None):
    ws.cell(row=row, column=1, value=a)
    ws.cell(row=row, column=2, value=b)
    ws.cell(row=row, column=3, value=c)
    ws.cell(row=row, column=4, value=d)
    ws.cell(row=row, column=5, value=e)


def test_extract_geodesy_price_entries_reads_price_in_tys_rub_converted_to_rub():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geodesy_row(
        ws, 1, a="1.1",
        b="Создание плановой опорной геодезической сети...",
        c="1 пункт", d=8,
        e=(
            "СБЦи5.6_0-1-8-3-2-1 Часть I, Глава 1, Таблица 8 п.3\n"
            "A=6.426 тыс.руб; \nКоличество = 8 (1 пункт)"
        ),
    )

    entries, skipped = extract_geodesy_price_entries(ws, first_row=1, last_row=1)

    assert skipped == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.table == "8"
    assert entry.item == "3"
    assert entry.price == pytest.approx(6426.0)  # 6.426 тыс.руб -> руб
    assert entry.category == "1.1"
    assert entry.source == "СБЦи5.6"


def test_extract_geodesy_price_entries_skips_rows_without_a_marker():
    # заголовок раздела и строка-коэффициент — нет "A=...тыс" в тексте
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geodesy_row(ws, 1, a="1", b="Раздел", e="Полевые работы")
    _write_geodesy_row(
        ws, 2, b="Коэффициенты", e="K1 = 0.7\nПрим. к табл.8 п.1 (Ценообразующий)"
    )

    entries, skipped = extract_geodesy_price_entries(ws, first_row=1, last_row=2)

    assert entries == []
    assert skipped == []  # это не пропуск, а строка не является позицией цены вовсе


def test_extract_geodesy_price_entries_skips_row_without_basis_text():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geodesy_row(ws, 1, a="1.8", b="Всего Полевые работы:")

    entries, skipped = extract_geodesy_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == []


def test_extract_geodesy_price_entries_uses_own_item_number_as_category_to_avoid_conflict(tmp_path):
    # реальная ситуация листа: Таблица 8 п.3 процитирована дважды
    # (поле/камералка) с разной ценой — category = № пп разводит их,
    # не давая конфликтовать при записи в БД
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geodesy_row(
        ws, 1, a="1.1", b="Полевая позиция", c="1 пункт", d=8,
        e="Часть I, Глава 1, Таблица 8 п.3\nA=6.426 тыс.руб; \nКоличество = 8 (1 пункт)",
    )
    _write_geodesy_row(
        ws, 2, a="4.4", b="Камеральная позиция", c="1 пункт", d=8,
        e="Часть I, Глава 1, Таблица 8 п.3\nA=2.538 тыс.руб; \nКоличество = 8 (1 пункт)",
    )

    entries, skipped = extract_geodesy_price_entries(ws, first_row=1, last_row=2)

    assert skipped == []
    assert len(entries) == 2
    by_category = {e.category: e for e in entries}
    assert by_category["1.1"].price == pytest.approx(6426.0)
    assert by_category["4.4"].price == pytest.approx(2538.0)

    conflicts = build_database(entries, [], tmp_path / "test.sqlite")
    assert conflicts == []


def test_extract_geodesy_price_entries_skips_ambiguous_basis():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_geodesy_row(
        ws, 1, a="9.9", b="Многозначное основание", c="ед.", d=1,
        e="Таблица 3 п.9 и п.8е ОУ\nA=1.0 тыс.руб; \nКоличество = 1 (ед.)",
    )

    entries, skipped = extract_geodesy_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_geodesy_index_entries_reuses_geology_period_not_row_text():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=74, column=5, value="Письмо Минстроя России №45358-ИФ/09 от 23.07.2026 г.")
    ws.cell(row=74, column=6, value="Коэф - т 7,19 от п.6 - 7")

    entries, warnings = extract_geodesy_index_entries(
        ws, geology_period=("2026-07-01", "2026-09-30"), row=74
    )

    assert warnings == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.work_type == "geodesy"
    assert entry.value == pytest.approx(7.19)
    assert entry.period_start == "2026-07-01"
    assert entry.period_end == "2026-09-30"


def test_extract_geodesy_index_entries_empty_cell_gives_warning_not_crash():
    wb = openpyxl.Workbook()
    ws = wb.active

    entries, warnings = extract_geodesy_index_entries(
        ws, geology_period=("2026-07-01", "2026-09-30"), row=74
    )

    assert entries == []


# --- экология (4-й модуль): "Таблица N, п.M" / "Таблица N, §M" ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Таблица 60, п.7, прим. 1", ("60", "7")),
        ("Таблица 60, §10", ("60", "10")),
        ("т. 70 § 62", ("70", "62")),
        (" Таблица 4, п.4", ("4", "4")),
        ("т. 70 §14, 22, 32, 38", (None, None)),  # ссылка сразу на несколько §
        ("п. 13 ОУ", (None, None)),  # нет номера таблицы вовсе
    ],
)
def test_parse_ecology_basis(text, expected):
    assert parse_ecology_basis(text) == expected


def _write_ecology_row(ws, row, a=None, b=None, d=None, e=None, f=None):
    # Столбцы листа "№2.5_ИЭИ": A=№пп, B=наименование, C=коэффициент/
    # единица (не используется парсером), D=объём, E=цена, F=обоснование.
    ws.cell(row=row, column=1, value=a)
    ws.cell(row=row, column=2, value=b)
    ws.cell(row=row, column=4, value=d)
    ws.cell(row=row, column=5, value=e)
    ws.cell(row=row, column=6, value=f)


def test_extract_ecology_price_entries_simple_row():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_ecology_row(
        ws, 1, a=10, b="Отбор точечных проб почв на химический анализ",
        d=5, e=6.9, f="Таблица 60, п.7, прим. 1",
    )

    entries, skipped = extract_ecology_price_entries(ws, first_row=1, last_row=1)

    assert skipped == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.source == "СБЦ-99"
    assert entry.table == "60"
    assert entry.item == "7"
    assert entry.price == pytest.approx(6.9)
    assert entry.category is None


def test_extract_ecology_price_entries_skips_section_header_and_coefficient_rows():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_ecology_row(ws, 1, a="IV. Отбор проб")  # заголовок раздела, item_no не число
    _write_ecology_row(ws, 2, b="к=", e=1.25, f="примечание 1")  # строка коэффициента

    entries, skipped = extract_ecology_price_entries(ws, first_row=1, last_row=2)

    assert entries == []
    assert skipped == []


def test_extract_ecology_price_entries_skips_row_without_basis_or_price():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_ecology_row(ws, 1, a=1, b="Позиция без основания", d=1, e=200)

    entries, skipped = extract_ecology_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_ecology_price_entries_uses_category_for_dual_priced_code(tmp_path):
    # Таблица 92 п.3 ("Радиационное обследование") цитируется дважды —
    # полевая (до kameral_start_row) и камеральная (после) цена.
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_ecology_row(
        ws, 5, a=1, b="Радиационное обследование участка",
        d=22.5, e=49.2, f="Таблица 92, п.3",
    )
    _write_ecology_row(
        ws, 15, a=2, b="Радиационное обследование участка (камеральная)",
        d=22.5, e=14.8, f="Таблица 92, п.3",
    )

    entries, skipped = extract_ecology_price_entries(
        ws, first_row=1, last_row=20, kameral_start_row=10,
    )

    assert skipped == []
    assert len(entries) == 2
    by_category = {e.category: e for e in entries}
    assert by_category["field"].price == pytest.approx(49.2)
    assert by_category["kameral"].price == pytest.approx(14.8)

    conflicts = build_database(entries, [], tmp_path / "test.sqlite")
    assert conflicts == []


def test_extract_ecology_price_entries_splits_reconnaissance_codes_and_skips_rows(tmp_path):
    # Т9 п.5 цитируется: ошибочной ссылкой в предполевых (доля 0,1 —
    # исключается skip_rows), полевой строкой и камеральной парой.
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_ecology_row(ws, 2, a=4, b="Разработка запросов. 10% от предполевых", e=0.1,
                       f="СБЦ-99 Таблица 9, п.5")
    _write_ecology_row(ws, 5, a=6, b="Почвенная рекогносцировка", d=14.7, e=4.33,
                       f="СБЦ-99 Таблица 9, п.5")
    _write_ecology_row(ws, 15, a=39, b="Камеральная обработка почвенной рекогносцировки",
                       d=14.7, e=1.69, f="Таблица 9, п.5")

    entries, skipped = extract_ecology_price_entries(
        ws, first_row=1, last_row=20, kameral_start_row=10, skip_rows={2},
    )

    assert skipped == [2]
    by_category = {e.category: e.price for e in entries}
    assert by_category == {"field": pytest.approx(4.33), "kameral": pytest.approx(1.69)}
    assert build_database(entries, [], tmp_path / "test.sqlite") == []


def test_extract_ecology_price_entries_skips_ambiguous_multi_item_basis():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_ecology_row(
        ws, 1, a=25, b="Определение агрохимического состава грунтов",
        d=200, e=158.5, f="т. 70 §14, 22, 32, 38",
    )

    entries, skipped = extract_ecology_price_entries(ws, first_row=1, last_row=1)

    assert entries == []
    assert skipped == [1]


def test_extract_ecology_index_entries_parses_value_and_quarter():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(
        row=93, column=2,
        value=(
            "Итого с учетом инфляционного коэффициента (Индексы изменения "
            "сметной стоимости изыскательских работ к уровню цен по "
            "состоянию на 01.01.1991 на III квартал 2026 г.) - 81,95"
        ),
    )

    entries, warnings = extract_ecology_index_entries(ws, row=93, value_column=2)

    assert warnings == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.work_type == "ecology"
    assert entry.value == pytest.approx(81.95)
    assert entry.period_start == "2026-07-01"
    assert entry.period_end == "2026-09-30"


def test_extract_ecology_index_entries_empty_cell_gives_warning_not_crash():
    wb = openpyxl.Workbook()
    ws = wb.active

    entries, warnings = extract_ecology_index_entries(ws, row=93, value_column=2)

    assert entries == []
    assert len(warnings) == 1
    assert len(warnings) == 1


# --- источник без подстановки по умолчанию и код возврата (Э4, 25.10.2026) ---

from data.build_references import (  # noqa: E402
    GEOLOGY_ASSUMED_SOURCE,
    REVIEWED_ISSUES,
    apply_geology_key_splits,
    collect_issues,
    price_conflict_groups,
    unreviewed_issues,
)


def test_row_without_source_is_marked_assumed_with_same_source():
    wb = openpyxl.Workbook()
    ws = wb.active
    _write_row(ws, 1, a=1, b="Бурение", d=10, e=36.0, f="Таблица 17, п.1")
    _write_row(ws, 2, a=2, b="Привязка", d=10, e=14.4, f="СБЦ-99 Таблица 93, п.3")
    entries, _ = extract_price_entries(ws, first_row=1, last_row=2)
    assert [(e.source, e.source_assumed) for e in entries] == [
        (GEOLOGY_ASSUMED_SOURCE, True), ("СБЦ-99", False),
    ]


def _entry(source, table, item, price, row, assumed=False):
    return PriceEntry(source, table, item, None, price, f"{source} Таблица {table}, п.{item}",
                      "позиция", row, assumed)


def test_unrecognized_source_is_an_issue():
    entries = [_entry("Применительно", "63", "7", 1.0, 10), _entry("СБЦ-99", "17", "1", 2.0, 11),
               _entry("СБЦ-99", "18", "4", 3.0, 12, assumed=True)]
    issues = collect_issues(entries, entries, [])
    assert [key for key, _ in issues] == [("source", "Применительно", "63", "7")]


def test_conflict_and_anchor_are_issues_and_anchor_never_reviewed():
    entries = [_entry("СБЦ-99", "9", "3", 47.2, 19), _entry("СБЦ-99", "9", "3", 23.4, 131)]
    assert list(price_conflict_groups(entries)) == [("СБЦ-99", "9", "3", None)]
    issues = collect_issues([], entries, ["Индекс ИГМИ не извлечён"])
    keys = [key for key, _ in issues]
    assert ("conflict", "СБЦ-99", "9", "3", None) in keys
    # с Э6а конфликт т.9 п.3 не «разобран»: в настоящей сборке его разводит
    # apply_geology_key_splits, а неразведённый — новая проблема
    new = unreviewed_issues(issues)
    assert len(new) == 2 and any("Индекс ИГМИ не извлечён" in m for m in new)


def test_new_conflict_is_not_reviewed():
    entries = [_entry("СБЦ-99", "17", "1", 36.0, 1), _entry("СБЦ-99", "17", "1", 38.0, 2)]
    assert unreviewed_issues(collect_issues([], entries, [])) != []


def test_reviewed_issues_cover_current_reference_data():
    # настоящая сборка из reference_data/: все текущие проблемы разобраны
    # (иначе build_references.py вернёт код 1 и setup.bat остановится)
    from data.build_references import ECOLOGY_SOURCE_XLSX, SHEET_NAME, SOURCE_XLSX

    if not SOURCE_XLSX.exists() or not ECOLOGY_SOURCE_XLSX.exists():
        pytest.skip("нет эталонных смет в reference_data/")
    ws = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)[SHEET_NAME]
    entries, _ = extract_price_entries(ws)
    entries, split_warnings = apply_geology_key_splits(entries)
    assert split_warnings == []
    issues = collect_issues(entries, entries, [])
    assert unreviewed_issues(issues) == []
    assert {key for key, _ in issues} <= REVIEWED_ISSUES


def test_main_exits_nonzero_on_new_issue(tmp_path, monkeypatch):
    import data.build_references as br

    monkeypatch.setattr(br, "DB_PATH", tmp_path / "references.sqlite")
    monkeypatch.setattr(br, "REVIEWED_ISSUES", frozenset())
    with pytest.raises(SystemExit) as exc:
        br.main()
    assert exc.value.code == 1
    assert (tmp_path / "references.sqlite").exists()


# --- разведение конфликтующих ключей листа ИГИ (Э6а, 30.10.2026) ---


def _geo_entry(table, item, price, row, name, source="СБЦ-99"):
    return PriceEntry(source, table, item, None, price, f"{source} Таблица {table}, п.{item}",
                      name, row)


def test_key_splits_give_each_work_its_own_key_without_conflicts():
    entries = [
        _geo_entry("9", "3", 47.2, 19, "Инженерно-геологическая рекогносцировка"),
        _geo_entry("9", "3", 23.4, 131, "Камеральная обработка инженерно-геологической рекогносцировки"),
        _geo_entry("57", "2", 1.6, 35, "Гидрогеологические наблюдения при бурении скважин"),
        _geo_entry("57", "2", 30.6, 41, "Отбор монолитов скальных грунтов."),
        _geo_entry("101", "5", 5700, 70, "Содержание базы экспедиции в течение2 месяца", "СБЦ"),
        _geo_entry("101", "5", 3600, 72, "Содержание перевалочной базы экспедиции", "СБЦ"),
        _geo_entry("17", "1", 36.0, 26, "Колонковое бурение"),
    ]
    assert len(price_conflict_groups(entries)) == 3
    split, warnings = apply_geology_key_splits(entries)
    assert warnings == []
    assert price_conflict_groups(split) == {}
    keys = {(e.source, e.table, e.item, e.category): e.price for e in split}
    assert keys == {
        ("СБЦ-99", "9", "3", "field"): 47.2,
        ("СБЦ-99", "9", "3", "kameral"): 23.4,
        ("СБЦ-99", "57", "2", "гидронаблюдения"): 1.6,
        ("СБЦ-99", "57", "2", None): 30.6,
        ("СБЦ", "101", "5", None): 5700,
        ("СБЦ", "101", "5", "перевалочная база"): 3600,
        ("СБЦ-99", "17", "1", None): 36.0,
    }


def test_key_split_rule_that_did_not_fire_is_reported():
    _, warnings = apply_geology_key_splits([_geo_entry("17", "1", 36.0, 26, "Бурение")])
    assert len(warnings) == 6  # ни одно правило не сработало — лист поменялся


def test_real_reference_build_keeps_all_prices_and_adds_split_keys(tmp_path):
    """Настоящая сборка: у каждой из шести позиций конфликтных ключей своя
    цена, монолиты скальных — 30,6 (лист 1.2.1_ИГИ!E41)."""
    import data.build_references as br
    from core.references import ReferenceBook

    if not br.SOURCE_XLSX.exists() or not br.ECOLOGY_SOURCE_XLSX.exists():
        pytest.skip("нет эталонных смет в reference_data/")
    db = tmp_path / "references.sqlite"
    assert br.build(db) == []
    book = ReferenceBook(db)
    assert book.get_price("СБЦ-99", "57", "2")[0] == 30.6
    assert book.get_price("СБЦ-99", "57", "2", "гидронаблюдения")[0] == 1.6
    assert book.get_price("СБЦ-99", "9", "3", "field")[0] == 47.2
    assert book.get_price("СБЦ-99", "9", "3", "kameral")[0] == 23.4
    assert book.get_price("СБЦ", "101", "5")[0] == 5700
    assert book.get_price("СБЦ", "101", "5", "перевалочная база")[0] == 3600
