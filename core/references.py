"""Справочник расценок и индексов на основе SQLite-базы, построенной
`data/build_references.py` из эталонной сметы (см. docs/etalon.md).
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "references.sqlite"

# Индекс пересчёта цен хранится в SQLite (таблица price_indices, см.
# data/build_references.py) диапазоном дат — индексы Минстроя публикуются
# одним значением сразу на весь квартал, см. записи разработки
# ("развилка про дату индекса решена"). Ставка НДС — так же, периодами с
# основанием (таблица vat_rates, data/build_references.py::VAT_RATES): до
# 30.10.2026 здесь была константа 20 % из подписи ячейки эталона (В-16).

# Открытый конец периода ставки НДС (как VAT_OPEN_END в build_references).
_OPEN_END = "9999-12-31"

# Версия формата справочника (таблица meta, С2 30.10.2026). Повышается, когда
# программе нужны новые таблицы/столбцы: справочник старой версии считается
# устаревшим и пересобирается (dev) или даёт понятную ошибку (exe).
# 1 — до 30.10.2026 (без meta); 2 — vat_rates + meta.
REFERENCES_SCHEMA_VERSION = 2


class ReferencesError(Exception):
    """Справочник отсутствует, повреждён или устарел и не пересобран —
    текст для оператора: что случилось и что сделать."""


def stale_reason(db_path: Path | str, fingerprint: str | None = None) -> str | None:
    """Почему справочник по пути `db_path` нельзя использовать как есть: нет
    файла, файл не читается как SQLite, версия формата не та, или (если
    передан `fingerprint` — отпечаток исходных файлов) собран из других
    исходников. None — справочник актуален."""
    path = Path(db_path)
    if not path.exists():
        return "файла справочника нет"
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return "справочник собран старой версией программы (нет таблицы meta)"
        return f"файл справочника не читается ({exc})"
    except sqlite3.DatabaseError as exc:
        return f"файл справочника повреждён ({exc})"
    version = rows.get("schema_version")
    if version != str(REFERENCES_SCHEMA_VERSION):
        return (
            f"версия формата справочника {version or 'не указана'}, программе нужна "
            f"{REFERENCES_SCHEMA_VERSION}"
        )
    if fingerprint is not None and rows.get("sources_fingerprint") != fingerprint:
        return "исходные эталоны или сборщик справочника изменились после сборки"
    return None


@dataclass(frozen=True)
class VatRecord:
    """Запись таблицы vat_rates: ставка НДС, период действия, основание."""

    rate: float
    period_start: str
    period_end: str
    basis: str

    def label(self) -> str:
        """«НДС 22 % — 425-ФЗ от 28.11.2025 (…), с 01.01.2026» — ставка и её
        основание для интерфейса и выгрузки."""
        start = date.fromisoformat(self.period_start)
        if self.period_end >= _OPEN_END:
            period = f"с {start:%d.%m.%Y}"
        else:
            period = f"{start:%d.%m.%Y}–{date.fromisoformat(self.period_end):%d.%m.%Y}"
        return f"НДС {round(self.rate * 100):d} % — {self.basis}, {period}"


@dataclass(frozen=True)
class IndexRecord:
    """Запись таблицы price_indices: значение индекса и период действия."""

    value: float
    period_start: str
    period_end: str
    basis: str

    def period_label(self) -> str:
        """«III кв. 2026», если период — ровно календарный квартал, иначе
        «01.07.2026–30.09.2026»."""
        start, end = date.fromisoformat(self.period_start), date.fromisoformat(self.period_end)
        quarter = (start.month - 1) // 3
        quarter_end = date(start.year, quarter * 3 + 3, 1)
        quarter_end = date(quarter_end.year, quarter_end.month,
                           calendar.monthrange(quarter_end.year, quarter_end.month)[1])
        if start.day == 1 and start.month == quarter * 3 + 1 and end == quarter_end:
            return f"{('I', 'II', 'III', 'IV')[quarter]} кв. {start.year}"
        return f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"


class ReferenceBook:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self._db_path = Path(db_path)
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"База справочника не найдена: {self._db_path}. "
                "Запустите data/build_references.py."
            )

    def get_price(
        self, source: str, table: str, item: str, category: str | None = None
    ) -> tuple[float, str]:
        """Вернуть (цена, полное текстовое основание) по коду расценки,
        например source="СБЦ-99", table="9", item="3". Для позиций,
        различающихся по категории (например, категориям грунта в одной
        и той же таблице/пункте), нужно передать category."""
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                'SELECT price, basis, name FROM prices '
                'WHERE source = ? AND "table" = ? AND item = ? '
                "AND COALESCE(category, '') = COALESCE(?, '')",
                (source, table, item, category),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row is None:
            raise KeyError(
                f"Расценка не найдена: source={source!r}, table={table!r}, "
                f"item={item!r}, category={category!r}"
            )
        price, basis, name = row
        return price, f"{basis} ({name})"

    def get_categories(self, source: str, table: str, item: str) -> list[tuple[str, float]]:
        """Вернуть все (категория, цена) для расценок с данным кодом,
        у которых заполнена категория — например, все категории
        грунта, реально встречающиеся в справочнике для конкретного
        кода бурения. Пустой список — легитимный ответ (например,
        для этого кода вообще нет позиций с категорией), не
        исключение."""
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                'SELECT category, price FROM prices '
                'WHERE source = ? AND "table" = ? AND item = ? AND category IS NOT NULL '
                "ORDER BY category",
                (source, table, item),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
        return [(category, price) for category, price in rows]

    def list_items(self, source: str, table: str) -> list[tuple[str, str | None, float, str]]:
        """Все позиции таблицы сборника: [(пункт, категория, цена,
        наименование)] по порядку пункта. Нужна, например, чтобы выбрать
        пункт по диапазону, записанному в наименовании («от 10 до 15 км»,
        core/object_data.py). Пустой список — легитимный ответ."""
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                'SELECT item, category, price, name FROM prices '
                'WHERE source = ? AND "table" = ? ORDER BY item, category',
                (source, table),
            ).fetchall()
        finally:
            conn.close()
        return [tuple(row) for row in rows]

    def all_prices(self) -> list[dict]:
        """Все расценки справочника (для просмотра на странице
        «Справочники», только чтение) по порядку сборника/таблицы/пункта."""
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                'SELECT source, "table", item, category, price, name, basis FROM prices '
                'ORDER BY source, "table", item, category'
            ).fetchall()
        finally:
            conn.close()
        keys = ("source", "table", "item", "category", "price", "name", "basis")
        return [dict(zip(keys, row)) for row in rows]

    def all_indices(self) -> list[dict]:
        """Все индексы пересчёта цен с периодами и основаниями (только чтение)."""
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT work_type, value, period_start, period_end, basis FROM price_indices "
                "ORDER BY work_type, period_start"
            ).fetchall()
        finally:
            conn.close()
        keys = ("work_type", "value", "period_start", "period_end", "basis")
        return [dict(zip(keys, row)) for row in rows]

    def get_index(self, work_type: str, date: str) -> float:
        """Вернуть индекс пересчёта для вида работ на указанную дату.

        Индексы публикуются письмами Минстроя одним значением сразу на
        весь квартал — ищем не точное совпадение даты, а попадание
        `date` в период действия (period_start..period_end
        включительно) какой-нибудь известной записи для этого
        work_type. Если дата ни в один известный период не попадает —
        `KeyError` (не гадаем, не берём ближайший индекс). Решение «взять
        последний известный» принимает вызывающий код явно — через
        `get_last_index` (см. core/pricing.py, политика индекса).

        Если периоды пересекаются (например, уточнённое письмо Минстроя
        внесено поверх квартального) — берётся запись с самым поздним
        `period_end` (`ORDER BY period_end DESC`), а не первая попавшаяся
        в порядке вставки."""
        return self.get_index_entry(work_type, date).value

    def get_index_entry(self, work_type: str, date: str) -> "IndexRecord":
        """Как `get_index`, но вместе с периодом действия и основанием."""
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT value, period_start, period_end, basis FROM price_indices "
                "WHERE work_type = ? AND period_start <= ? AND period_end >= ? "
                "ORDER BY period_end DESC, period_start DESC",
                (work_type, date, date),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row is None:
            raise KeyError(
                f"Индекс пересчёта цен не найден на дату {date} "
                f"для work_type={work_type!r}"
            )
        return IndexRecord(*row)

    def get_last_index(self, work_type: str, date: str) -> "IndexRecord":
        """Последний известный индекс для `work_type`, период которого
        закончился ДО `date` (для даты расчёта позже всех внесённых
        периодов — актуальный индекс ещё не внесён в справочник).

        Если `date` раньше всех известных периодов — `KeyError`: «последнего
        известного» на такую дату нет, более поздний индекс к прошлой дате
        не применяем."""
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT value, period_start, period_end, basis FROM price_indices "
                "WHERE work_type = ? AND period_end < ? "
                "ORDER BY period_end DESC, period_start DESC",
                (work_type, date),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row is None:
            raise KeyError(
                f"Нет ни одного индекса пересчёта цен для work_type={work_type!r} "
                f"с периодом до {date}"
            )
        return IndexRecord(*row)

    def get_vat_rate(self, date: str) -> float:
        """Вернуть ставку НДС, действующую на указанную дату."""
        return self.get_vat_entry(date).rate

    def get_vat_entry(self, date: str) -> VatRecord:
        """Ставка НДС на дату вместе с периодом и основанием. Дата не попадает
        ни в один внесённый период — `KeyError` (не угадываем ставку). Нет
        таблицы vat_rates (справочник собран до 30.10.2026) — тоже `KeyError`
        с подсказкой пересобрать: итог без НДС, предупреждение — в pricing."""
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT rate, period_start, period_end, basis FROM vat_rates "
                "WHERE period_start <= ? AND period_end >= ? "
                "ORDER BY period_start DESC",
                (date, date),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            raise KeyError(
                f"в справочнике нет ставок НДС ({exc}) — пересоберите справочник: "
                "python data/build_references.py"
            ) from exc
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"Ставка НДС не найдена на дату {date}")
        return VatRecord(*row)

    def all_vat_rates(self) -> list[VatRecord]:
        """Все периоды ставки НДС с основаниями (только чтение)."""
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT rate, period_start, period_end, basis FROM vat_rates ORDER BY period_start"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        return [VatRecord(*row) for row in rows]
