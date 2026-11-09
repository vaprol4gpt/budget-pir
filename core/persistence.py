"""Сохранение расчётов между запусками программы ("сохранённая смета").

Сохраняется НЕ результат расчёта, а вход: имя, код модуля, введённые
оператором данные (`input_data`) и список правок (`Edit` из
`core/overrides.py`). При открытии смета не десериализуется как
готовый `EstimateLine` — она пересчитывается заново из сохранённого
`input_data` через `build_volumes`/`build_estimate` нужного модуля, а
затем поверх накатываются сохранённые правки через `OverrideLog`
(см. `app/streamlit_app.py`). `EstimateLine` никогда не сериализуется
напрямую — это прямое следствие того, что модуль представляет собой
чистую функцию от входа (тот же принцип, что и у
`core/models.py::calculate_amount`, `core/overrides.py::effective_estimate`).

**Исключение — версии многоэтапного расчёта** (`save_version`,
таблица `smeta_versions`, см. `core/stages.py`): подтверждённый этап
сохраняется СНИМКОМ строк (ведомость/смета) вместе с его входом и
правками. Это принятое решение (№7, `docs/multistage_design.md`):
утверждённая ведомость — самостоятельный документ и не должна тихо
меняться при пересчёте. Одноэтапные сметы (`save_smeta`) — по-прежнему
только вход.

Отдельный файл БД (`data/smety.sqlite`), не `data/references.sqlite`:
это невоспроизводимые пользовательские данные (нет скрипта, который
бы их пересоздал из первоисточника, в отличие от справочника цен). В
git НЕ коммитится (решение 14.10.2026: реальные сметы могут быть
конфиденциальными, см. `.gitignore`) — живёт только на своём ПК, при
переходе на другой ПК переносится вручную.

**Версия схемы** (таблица `schema_version`, Э4 дорожной карты,
25.10.2026): при открытии база доводится до `SCHEMA_VERSION`
(обратно совместимые миграции — добавление столбцов); база новее
программы — `SmetaStoreFormatError`, а не порча данных. Связи между
таблицами проверяются (`PRAGMA foreign_keys=ON` на каждом соединении).

**Несколько экземпляров программы одновременно** (см.
записи разработки): начиная с этой
версии `app_launcher.py` умеет открывать несколько окон программы
сразу, каждое на своём порту (см. `app_launcher.py::_find_free_port`)
— но все экземпляры по-прежнему пишут в ОДИН И ТОТ ЖЕ
`data/smety.sqlite`. Чтобы это было безопасно: (1) каждое соединение
явно включает `PRAGMA journal_mode=WAL` — штатный режим SQLite,
рассчитанный именно на параллельный доступ нескольких процессов
(писатели не блокируют читателей, в отличие от классического
rollback-журнала); (2) операции записи (`save_smeta`, `save_edits`)
обёрнуты в повтор при `sqlite3.OperationalError` ("database is
locked") — на случай, если ДВА процесса всё же попытаются писать в
один и тот же момент (WAL допускает только ОДНОГО одновременного
писателя, вторая параллельная запись всё равно может кратковременно
упереться в блокировку). Если повторы не помогли — наружу летит
`SmetaStoreBusyError` (не голый `sqlite3.OperationalError` — так
`app/streamlit_app.py` может поймать именно эту, ожидаемую ситуацию
и показать `st.error` оператору, не уронив всё приложение).

**Путь по умолчанию и упаковка в exe (PyInstaller, см.
записи разработки):** при обычном запуске `python ...`
путь берётся относительно этого файла (`data/smety.sqlite` в корне
проекта), как и раньше. При запуске из PyInstaller-сборки (`--onefile`
или `--onedir`, `sys.frozen is True`) `__file__` указывает НЕ на
реальное расположение программы, а на временную папку распаковки
(`--onefile`) — файл там пропадает при каждом закрытии программы,
значит сохранённые сметы руководителя терялись бы после каждого
перезапуска. Поэтому в frozen-режиме база явно кладётся РЯДОМ с самим
exe (`Path(sys.executable).parent`), а не рядом с `__file__` —
`data/references.sqlite` эта проблема НЕ касается (справочник —
пересоздаваемые данные, `core/references.py` намеренно не трогается,
см. жёсткие границы задания)."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from core.overrides import Edit
from core.stages import SnapshotFormatError, StageResult, lines_from_json, lines_to_json, make_result

T = TypeVar("T")

# Сколько раз повторить операцию записи при "database is locked" и с
# какой (растущей) паузой между попытками — консервативный выбор,
# зафиксирован в записи разработки: 3 попытки — обычно
# достаточно пережить короткую конкурентную запись другого экземпляра
# (сама запись — доли секунды), при этом оператор не ждёт заметно
# долго, если что-то действительно застряло; пауза растёт (0.2 -> 0.4
# -> 0.8 с) вместо фиксированной — не долбить в занятую базу с той же
# частотой, но и не ждать секундами при обычной, мгновенно проходящей
# конкуренции.
_MAX_WRITE_RETRIES = 3
_RETRY_BASE_DELAY_SECONDS = 0.2

# `sqlite3.connect(..., timeout=...)` — сколько САМ SQLite-драйвер
# внутренне ждёт снятия блокировки ПЕРЕД тем, как поднять
# `OperationalError`, на КАЖДУЮ попытку. По умолчанию у sqlite3 это
# 5.0 с — вместе с нашим собственным повтором (3 попытки) это дало бы
# неоправданно долгое worst-case ожидание (до ~15 с). Держим короче
# (2 с) — так наш ЯВНЫЙ повтор с растущей паузой (см. выше) остаётся
# основным механизмом устойчивости, а внутренний busy-wait драйвера —
# лишь небольшой дополнительный запас на каждую попытку, не
# доминирующий над общим временем ожидания.
_CONNECTION_TIMEOUT_SECONDS = 2.0

# Версия схемы data/smety.sqlite (таблица schema_version):
# 1 — до 25.10.2026 (таблицы smety, edits, smeta_versions, drafts);
# 2 — smeta_versions.calc_date (дата расчёта, Э1 дорожной карты);
# 3 — smeta_versions.object_data, drafts.object_data (данные объекта, Э5);
# 4 — таблица cost_calcs (расчёт себестоимости при версии, Э6).
SCHEMA_VERSION = 4

# Столбцы, добавленные миграциями: (таблица, столбец, тип). Добавляются,
# если их нет, — обратно совместимо, у прежних строк NULL.
_ADDED_COLUMNS = (
    ("smeta_versions", "calc_date", "TEXT"),
    ("smeta_versions", "object_data", "TEXT"),
    ("drafts", "object_data", "TEXT"),
)


def _edit_to_dict(edit: Edit) -> dict:
    """Правка для JSON. `custom` (данные своей строки, Э7) пишется только у
    записей своей строки — обычные правки сохраняются в прежнем виде, и их
    по-прежнему читает прежняя версия программы. Схема базы не меняется:
    свои строки живут в том же столбце `edits` версии и черновика."""
    data = dataclasses.asdict(edit)
    if data.get("custom") is None:
        data.pop("custom", None)
    return data


def _edits_to_json(edits) -> str:
    return json.dumps([_edit_to_dict(edit) for edit in edits], ensure_ascii=False)


_EDIT_FIELDS = frozenset(f.name for f in dataclasses.fields(Edit))


def _edits_from_json(raw: str) -> list[Edit]:
    edits = json.loads(raw)
    for edit in edits:
        unknown = set(edit) - _EDIT_FIELDS
        if unknown:
            raise SnapshotFormatError(
                "журнал правок сохранён более новой версией программы (неизвестные поля: "
                f"{', '.join(sorted(unknown))}) — обновите программу."
            )
    return [Edit(**edit) for edit in edits]


def _json_or_none(value: dict | None) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


@dataclasses.dataclass(frozen=True)
class DraftRecord:
    """Черновик многоэтапного расчёта (см. SmetaStore.save_draft)."""

    id: int
    name: str
    module_code: str
    stage_key: str
    inputs: dict
    edits: list[Edit]
    base_version_id: int | None
    saved_at: str
    # Данные объекта на момент сохранения (core/object_data.py, словарь);
    # None — черновик сохранён без них (в т.ч. до Э5).
    object_data: dict | None = None


class SmetaStoreFormatError(RuntimeError):
    """База сохранённых смет создана более новой версией программы
    (`schema_version` больше `SCHEMA_VERSION`) — эта версия её не
    меняет и не читает, чтобы не испортить данные."""


class SmetaStoreBusyError(RuntimeError):
    """`data/smety.sqlite` занята другим процессом дольше отведённого
    времени повторов — например, несколько экземпляров `BudgetPIR.exe`
    пишут одновременно. Отдельный тип исключения (не голый
    `sqlite3.OperationalError`) — чтобы вызывающий код (интерфейс)
    мог поймать именно эту ожидаемую ситуацию и показать оператору
    понятное сообщение (`st.error`), не уронив всё приложение."""


def _default_db_path() -> Path:
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "data" / "smety.sqlite"


DEFAULT_DB_PATH = _default_db_path()


class SmetaStore:
    """Хранилище сохранённых смет поверх SQLite.

    В отличие от `ReferenceBook` (требует уже собранной
    `data/references.sqlite`, падает если её нет — она пересоздаётся
    отдельным скриптом наполнения) `SmetaStore` создаёт свои таблицы
    сам при первом использовании, если их ещё нет: `data/smety.sqlite`
    не пересоздаётся никаким скриптом, это единственный источник
    сохранённых смет — файл и таблицы должны появиться сами при первом
    сохранении, а не требовать отдельного шага "инициализировать базу".
    """

    def __init__(self, db_path: Path | str | None = None):
        # `db_path` резолвится здесь, а не через `= DEFAULT_DB_PATH` в
        # сигнатуре: значение по умолчанию аргумента вычисляется ОДИН
        # РАЗ при импорте модуля и не видит последующих переприсваиваний
        # `core.persistence.DEFAULT_DB_PATH` (например, monkeypatch в
        # тестах/смоук-скриптах) — уже дважды приводило к тому, что
        # такая подмена молча не срабатывала и код писал в реальную
        # `data/smety.sqlite` (см. записи разработки, шаг 4, и
        # записи разработки, шаг 3).
        self._db_path = Path(db_path if db_path is not None else DEFAULT_DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._backup_before_migration()
            self._create_tables()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                raise
            raise SmetaStoreFormatError(self._damaged_text(exc)) from exc
        except sqlite3.DatabaseError as exc:
            raise SmetaStoreFormatError(self._damaged_text(exc)) from exc

    def _damaged_text(self, exc: Exception) -> str:
        return (f"файл {self._db_path} повреждён или это не база смет ({exc}). Файл не изменён: "
                "восстановите его из своей копии или перенесите в другую папку — тогда программа "
                "создаст новую пустую базу.")

    def _stored_schema_version(self) -> int | None:
        """Версия схемы в файле базы; таблицы версий (или файла) нет — None. Без записи."""
        if not self._db_path.exists() or self._db_path.stat().st_size == 0:
            return None
        conn = sqlite3.connect(self._db_path, timeout=_CONNECTION_TIMEOUT_SECONDS)
        try:
            found = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if found is None:
                return None
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _backup_before_migration(self) -> None:
        """Сметы живут только в этом файле: перед обновлением схемы — его копия рядом
        («smety_до_схемы_v4.sqlite»). База новее программы — отказ до любого изменения
        файла (раньше проверка шла после CREATE TABLE IF NOT EXISTS)."""
        current = self._stored_schema_version()
        if current is None:
            return
        if current > SCHEMA_VERSION:
            raise SmetaStoreFormatError(
                f"База сохранённых смет {self._db_path} создана более новой версией "
                f"программы (схема v{current}, эта версия знает до v{SCHEMA_VERSION}). "
                "Обновите программу; база не изменена."
            )
        if current < SCHEMA_VERSION:
            # Расширение .sqlite — чтобы копия смет подпадала под data/*.sqlite в .gitignore.
            backup = self._db_path.with_name(f"{self._db_path.stem}_до_схемы_v{SCHEMA_VERSION}.sqlite")
            if not backup.exists():
                source = sqlite3.connect(self._db_path, timeout=_CONNECTION_TIMEOUT_SECONDS)
                target = sqlite3.connect(backup)
                try:
                    source.backup(target)  # учитывает незавершённый журнал WAL, в отличие от копии файла
                finally:
                    target.close()
                    source.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=_CONNECTION_TIMEOUT_SECONDS)
        # WAL — persist-настройка (хранится в самом файле БД), но
        # выставляем на каждом соединении: дёшево и идемпотентно, и
        # тогда не важно, каким соединением/когда файл был впервые
        # создан (например, старой версией кода до этого изменения).
        conn.execute("PRAGMA journal_mode=WAL")
        # Проверка связей (parent_id, base_version_id, smeta_id) — по
        # умолчанию в SQLite выключена и включается на каждом соединении.
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _run_write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Выполняет операцию записи (`operation`) с повтором при
        `sqlite3.OperationalError` "database is locked" — см. докстринг
        модуля про несколько экземпляров программы. `operation`
        получает открытое соединение, сама решает, что в нём делать
        (INSERT/DELETE/executemany), но НЕ коммитит и НЕ закрывает —
        это делает `_run_write` централизованно, чтобы откат при
        ошибке и повтор были одинаковыми для всех операций записи."""
        delay = _RETRY_BASE_DELAY_SECONDS
        last_error: sqlite3.OperationalError | None = None

        for attempt in range(1, _MAX_WRITE_RETRIES + 1):
            conn: sqlite3.Connection | None = None
            try:
                # `_connect()` тоже может бросить "database is locked"
                # (сам `PRAGMA journal_mode=WAL` требует блокировки на
                # запись) — раньше это происходило ВНЕ этого try и не
                # попадало под повтор, что и приводило к необработанному
                # падению при живой проверке нескольких экземпляров
                # (см. записи разработки).
                conn = self._connect()
                result = operation(conn)
                conn.commit()
                return result
            except sqlite3.OperationalError as exc:
                if conn is not None:
                    conn.rollback()
                if "locked" not in str(exc).lower():
                    raise  # другая ошибка — не наш случай, не глотаем
                last_error = exc
                if attempt < _MAX_WRITE_RETRIES:
                    time.sleep(delay)
                    delay *= 2
            finally:
                if conn is not None:
                    conn.close()

        raise SmetaStoreBusyError(
            f"Не удалось записать в базу сохранённых смет после "
            f"{_MAX_WRITE_RETRIES} попыток — файл занят другим "
            f"процессом (возможно, открыт ещё один экземпляр "
            f"программы). Попробуйте сохранить ещё раз через "
            f"несколько секунд."
        ) from last_error

    def _create_tables(self) -> None:
        # Через `_run_write` — та же защита от "database is locked",
        # что и у save_smeta/save_edits. Найдено при проверке
        # нескольких одновременных экземпляров (см.
        # записи разработки): ДВА процесса,
        # запущенные почти одновременно, оба создают `SmetaStore()` на
        # старте (конструктор сам вызывает `_create_tables()`) — если
        # это происходит буквально в один момент, один из них раньше
        # без этой защиты падал с необработанным
        # `sqlite3.OperationalError` прямо в конструкторе, до того как
        # вообще что-то успевал сохранить.
        def _create(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS smety (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    module_code TEXT NOT NULL,
                    input_data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Черновики (см. save_draft): один на пару (модуль, имя),
            # новое сохранение перезаписывает строку — UNIQUE +
            # ON CONFLICT DO UPDATE, id при перезаписи сохраняется.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    stage_key TEXT NOT NULL,
                    inputs TEXT NOT NULL,
                    edits TEXT NOT NULL,
                    base_version_id INTEGER,
                    saved_at TEXT NOT NULL,
                    object_data TEXT,
                    UNIQUE (module_code, name),
                    FOREIGN KEY (base_version_id) REFERENCES smeta_versions (id)
                )
                """
            )
            # Цепочка версий многоэтапного расчёта (см. save_version):
            # одна строка = один подтверждённый этап, `parent_id` —
            # версия, от которой продолжили работу.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS smeta_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER,
                    name TEXT NOT NULL,
                    module_code TEXT NOT NULL,
                    stage_key TEXT NOT NULL,
                    inputs TEXT NOT NULL,
                    edits TEXT NOT NULL,
                    lines TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    based_on TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    calc_date TEXT,
                    object_data TEXT,
                    FOREIGN KEY (parent_id) REFERENCES smeta_versions (id)
                )
                """
            )
            # Расчёт себестоимости (минимального бюджета) при версии сметы
            # (Э6): ввод оператора и итоговые числа; несколько сохранений
            # на одну версию — история, берётся последнее.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cost_calcs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id INTEGER NOT NULL,
                    module_code TEXT NOT NULL,
                    inputs TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    FOREIGN KEY (version_id) REFERENCES smeta_versions (id)
                )
                """
            )
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = row[0] if row and row[0] is not None else 1
            if current > SCHEMA_VERSION:
                raise SmetaStoreFormatError(
                    f"База сохранённых смет {self._db_path} создана более новой версией "
                    f"программы (схема v{current}, эта версия знает до v{SCHEMA_VERSION}). "
                    "Обновите программу; база не изменена."
                )
            # v1 → v2 → v3: добавленные столбцы (_ADDED_COLUMNS). Обратно
            # совместимо: у прежних строк NULL («не записано»), сами строки
            # не меняются. Проверка по столбцу, а не по номеру: база,
            # созданная промежуточным кодом, столбец уже может иметь.
            for table, column, column_type in _ADDED_COLUMNS:
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
            if current < SCHEMA_VERSION or row[0] is None:
                conn.execute("DELETE FROM schema_version")
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS edits (
                    smeta_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    line_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    old_value REAL NOT NULL,
                    new_value REAL NOT NULL,
                    reason TEXT,
                    FOREIGN KEY (smeta_id) REFERENCES smety (id)
                )
                """
            )

        self._run_write(_create)

    def save_smeta(self, name: str, module_code: str, input_data: dict) -> int:
        """Создаёт НОВУЮ запись сметы, возвращает её id.

        Не перезаписывает существующую запись с тем же именем (МВП:
        "Сохранить" всегда создаёт новую версию, а не ищет и
        обновляет старую по имени — проще и безопаснее для оператора,
        случайно не потерять предыдущую сохранённую версию; выбор
        зафиксирован в записи разработки, не баг).

        При конкурентной записи от другого экземпляра программы —
        несколько попыток повтора (см. `_run_write`), и только если
        не помогло — `SmetaStoreBusyError`."""

        def _insert(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                "INSERT INTO smety (name, module_code, input_data, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    name,
                    module_code,
                    json.dumps(input_data, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.lastrowid

        return self._run_write(_insert)

    def save_edits(self, smeta_id: int, edits: list[Edit]) -> None:
        """Сохраняет текущий список правок для сметы.

        Удаляет прежние записи правок этой сметы и записывает список
        заново целиком (не точечный diff) — проще и надёжнее для МВП,
        т.к. `OverrideLog.history()` и так уже даёт полный
        актуальный список на момент сохранения.

        При конкурентной записи от другого экземпляра программы —
        несколько попыток повтора (см. `_run_write`), и только если
        не помогло — `SmetaStoreBusyError`."""

        def _replace(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM edits WHERE smeta_id = ?", (smeta_id,))
            conn.executemany(
                "INSERT INTO edits "
                "(smeta_id, position, line_id, field, old_value, new_value, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        smeta_id,
                        position,
                        edit.line_id,
                        edit.field,
                        edit.old_value,
                        edit.new_value,
                        edit.reason,
                    )
                    for position, edit in enumerate(edits)
                ],
            )

        self._run_write(_replace)

    def load_smeta(self, smeta_id: int) -> tuple[str, dict, list[Edit]]:
        """Вернуть (module_code, input_data, edits) сохранённой сметы.

        `edits` восстанавливаются в исходном порядке применения
        (`ORDER BY position`) — порядок важен для `OverrideLog`, т.к.
        при нескольких правках на одно и то же поле побеждает
        последняя (см. `core/overrides.py`)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT module_code, input_data FROM smety WHERE id = ?", (smeta_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Смета с id={smeta_id} не найдена")
            module_code, input_data_json = row

            edit_rows = conn.execute(
                "SELECT line_id, field, old_value, new_value, reason FROM edits "
                "WHERE smeta_id = ? ORDER BY position",
                (smeta_id,),
            ).fetchall()
        finally:
            conn.close()

        input_data = json.loads(input_data_json)
        edits = [
            Edit(line_id=line_id, field=field, old_value=old_value, new_value=new_value, reason=reason)
            for line_id, field, old_value, new_value, reason in edit_rows
        ]
        return module_code, input_data, edits

    def list_smety(self) -> list[tuple[int, str, str, str]]:
        """Список (id, name, module_code, created_at) для отображения
        в интерфейсе — новые сметы сверху (`ORDER BY created_at DESC`)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, module_code, created_at FROM smety ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return rows

    # --- цепочка версий многоэтапного расчёта (core/stages.py) ---
    #
    # Каждое подтверждение этапа — новая версия со ссылкой на
    # родительскую (`parent_id`): от какой версии оператор продолжил
    # работу. Версия хранит СНИМОК подтверждённого результата этапа
    # (строки), а не только вход — осознанное расширение принципа
    # "храним вход, не результат" (решение №7, docs/multistage_design.md):
    # утверждённая ведомость — самостоятельный документ, она не должна
    # меняться от того, что после подтверждения поменялся справочник
    # или код генерации. Версии неизменяемы — только INSERT, без UPDATE.
    # Ветвление разрешено: от одной версии можно продолжить дважды —
    # это и есть задел под сравнение вариантов расчёта.
    #
    # Прежние save_smeta/save_edits/load_smeta (одноэтапные модули)
    # работают как раньше, в отдельных таблицах.

    def save_version(
        self,
        name: str,
        module_code: str,
        parent_id: int | None,
        result: StageResult,
        calc_date: str | None = None,
        object_data: dict | None = None,
    ) -> int:
        """Сохраняет подтверждённый этап как новую версию, возвращает её id.
        `parent_id` должен существовать (или быть `None` для первой
        версии цепочки) и относиться к тому же модулю. `calc_date` — дата
        расчёта (ISO, по ней берётся индекс пересчёта цен), `None` — не
        задана. `object_data` — данные объекта (словарь
        core/object_data.py::ObjectData.to_dict), `None` — не заданы."""

        def _insert(conn: sqlite3.Connection) -> int:
            if parent_id is not None:
                row = conn.execute(
                    "SELECT module_code FROM smeta_versions WHERE id = ?", (parent_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Родительская версия id={parent_id} не найдена")
                if row[0] != module_code:
                    raise ValueError(
                        f"Родительская версия id={parent_id} — модуль '{row[0]}', "
                        f"а сохраняется '{module_code}'"
                    )
            cursor = conn.execute(
                "INSERT INTO smeta_versions (parent_id, name, module_code, stage_key, "
                "inputs, edits, lines, fingerprint, based_on, confirmed_at, calc_date, "
                "object_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    parent_id,
                    name,
                    module_code,
                    result.stage_key,
                    json.dumps(dict(result.inputs), ensure_ascii=False),
                    _edits_to_json(result.edits),
                    json.dumps(lines_to_json(result.lines), ensure_ascii=False),
                    result.fingerprint,
                    json.dumps(dict(result.based_on)),
                    result.confirmed_at,
                    calc_date,
                    _json_or_none(object_data),
                ),
            )
            return cursor.lastrowid

        return self._run_write(_insert)

    def load_version_chain(self, version_id: int) -> tuple[str, str, list[StageResult]]:
        """(module_code, name, снимки) — цепочка от первой версии до
        `version_id` включительно, по порядку подтверждения. Снимки
        восстанавливаются с сохранёнными отпечатком и временем
        подтверждения (не пересчитываются)."""
        conn = self._connect()
        try:
            chain_rows = []
            current: int | None = version_id
            while current is not None:
                row = conn.execute(
                    "SELECT id, parent_id, name, module_code, stage_key, inputs, edits, "
                    "lines, fingerprint, based_on, confirmed_at "
                    "FROM smeta_versions WHERE id = ?",
                    (current,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Версия с id={current} не найдена")
                chain_rows.append(row)
                current = row[1]
        finally:
            conn.close()

        chain_rows.reverse()
        # SnapshotFormatError (core/stages.py) — снимок в формате, который
        # эта версия не читает: летит наружу с номером версии, интерфейс
        # показывает его оператору.
        results = []
        for (_id, _parent, _name, _module, stage_key, inputs, edits, lines,
             fingerprint_value, based_on, confirmed_at) in chain_rows:
            try:
                version_lines = lines_from_json(json.loads(lines))
            except SnapshotFormatError as exc:
                raise SnapshotFormatError(f"Версия #{_id}: {exc}") from exc
            result = make_result(
                stage_key,
                version_lines,
                json.loads(inputs),
                _edits_from_json(edits),
                json.loads(based_on),
                confirmed_at,
            )
            results.append(dataclasses.replace(result, fingerprint=fingerprint_value))
        last = chain_rows[-1]
        return last[3], last[2], results

    # --- расчёт себестоимости при версии (Э6) ---

    def save_cost_calc(self, version_id: int, module_code: str, inputs: dict, summary: dict) -> int:
        """Сохраняет расчёт себестоимости при версии сметы `version_id`
        (того же модуля), возвращает id записи."""

        def _insert(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT module_code FROM smeta_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Версия id={version_id} не найдена")
            if row[0] != module_code:
                raise ValueError(
                    f"Версия id={version_id} — модуль '{row[0]}', а расчёт — '{module_code}'"
                )
            cursor = conn.execute(
                "INSERT INTO cost_calcs (version_id, module_code, inputs, summary, saved_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    version_id, module_code, json.dumps(inputs, ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False), datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.lastrowid

        return self._run_write(_insert)

    def load_cost_calc_for_chain(self, version_id: int) -> tuple[dict, dict, int] | None:
        """Последний расчёт себестоимости по цепочке версии `version_id`
        (сама версия и её предки): (ввод, итог, id версии, при которой
        сохранён) или None."""
        conn = self._connect()
        try:
            current: int | None = version_id
            while current is not None:
                row = conn.execute(
                    "SELECT inputs, summary FROM cost_calcs WHERE version_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (current,),
                ).fetchone()
                if row is not None:
                    return json.loads(row[0]), json.loads(row[1]), current
                parent = conn.execute(
                    "SELECT parent_id FROM smeta_versions WHERE id = ?", (current,)
                ).fetchone()
                current = parent[0] if parent else None
        finally:
            conn.close()
        return None

    def get_version_object_data(self, version_id: int) -> dict | None:
        """Данные объекта, сохранённые с версией (`None` — без них)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT object_data FROM smeta_versions WHERE id = ?", (version_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"Версия с id={version_id} не найдена")
        return json.loads(row[0]) if row[0] else None

    def get_version_calc_date(self, version_id: int) -> str | None:
        """Дата расчёта, сохранённая с версией (`None` — версия сохранена
        без даты, в т.ч. до появления этого поля)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT calc_date FROM smeta_versions WHERE id = ?", (version_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"Версия с id={version_id} не найдена")
        return row[0]

    def list_versions(self, module_code: str | None = None) -> list[tuple[int, int | None, str, str, str, str]]:
        """Список (id, parent_id, name, module_code, stage_key,
        confirmed_at) — новые сверху."""
        conn = self._connect()
        try:
            query = (
                "SELECT id, parent_id, name, module_code, stage_key, confirmed_at "
                "FROM smeta_versions"
            )
            params: tuple = ()
            if module_code is not None:
                query += " WHERE module_code = ?"
                params = (module_code,)
            rows = conn.execute(query + " ORDER BY id DESC", params).fetchall()
        finally:
            conn.close()
        return rows

    # --- черновики многоэтапного расчёта ---
    #
    # Черновик — НЕподтверждённый ввод оператора на ОДНОМ этапе (ввод
    # формы этапа + правки строк этого этапа), без снимка результата и
    # без версии: отдельная таблица `drafts`, в `smeta_versions` не
    # пишет. Подтверждённые более ранние этапы черновик не копирует —
    # он ссылается на версию (`base_version_id`), от которой оператор
    # продолжал работу; при открытии они восстанавливаются из цепочки
    # версий как есть. Один черновик на пару (модуль, имя): повторное
    # сохранение перезаписывает его, история не накапливается. В
    # сводный бюджет черновик не попадает — там только подтверждённые
    # версии.

    def save_draft(
        self,
        name: str,
        module_code: str,
        stage_key: str,
        inputs: dict,
        edits: list[Edit],
        base_version_id: int | None,
        object_data: dict | None = None,
    ) -> int:
        """Сохраняет (или перезаписывает) черновик, возвращает его id —
        один и тот же для одной пары (модуль, имя). `object_data` — данные
        объекта (словарь), `None` — не заданы."""

        def _upsert(conn: sqlite3.Connection) -> int:
            if base_version_id is not None:
                row = conn.execute(
                    "SELECT module_code FROM smeta_versions WHERE id = ?", (base_version_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Версия id={base_version_id} не найдена")
                if row[0] != module_code:
                    raise ValueError(
                        f"Версия id={base_version_id} — модуль '{row[0]}', "
                        f"а черновик — '{module_code}'"
                    )
            conn.execute(
                "INSERT INTO drafts (module_code, name, stage_key, inputs, edits, "
                "base_version_id, saved_at, object_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (module_code, name) DO UPDATE SET "
                "stage_key = excluded.stage_key, inputs = excluded.inputs, "
                "edits = excluded.edits, base_version_id = excluded.base_version_id, "
                "saved_at = excluded.saved_at, object_data = excluded.object_data",
                (
                    module_code,
                    name,
                    stage_key,
                    json.dumps(inputs, ensure_ascii=False),
                    _edits_to_json(edits),
                    base_version_id,
                    datetime.now(timezone.utc).isoformat(),
                    _json_or_none(object_data),
                ),
            )
            return conn.execute(
                "SELECT id FROM drafts WHERE module_code = ? AND name = ?", (module_code, name)
            ).fetchone()[0]

        return self._run_write(_upsert)

    def load_draft(self, draft_id: int) -> DraftRecord:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, name, module_code, stage_key, inputs, edits, base_version_id, "
                "saved_at, object_data FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"Черновик с id={draft_id} не найден")
        draft_id, name, module_code, stage_key, inputs, edits, base_version_id, saved_at, object_data = row
        return DraftRecord(
            id=draft_id,
            name=name,
            module_code=module_code,
            stage_key=stage_key,
            inputs=json.loads(inputs),
            edits=_edits_from_json(edits),
            base_version_id=base_version_id,
            saved_at=saved_at,
            object_data=json.loads(object_data) if object_data else None,
        )

    def list_drafts(self) -> list[tuple[int, str, str, str, str]]:
        """Список (id, name, module_code, stage_key, saved_at) — свежие сверху."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, module_code, stage_key, saved_at FROM drafts "
                "ORDER BY saved_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return rows
