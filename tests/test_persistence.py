import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

import core.persistence as persistence_module
from core.overrides import Edit
from core.persistence import SmetaStore, SmetaStoreBusyError, _default_db_path
from core.stages import SOURCE_INPUT, VolumeLine, make_result


def make_store(tmp_path) -> SmetaStore:
    return SmetaStore(tmp_path / "test_smety.sqlite")


def test_default_db_path_resolves_next_to_exe_when_frozen(monkeypatch):
    # PyInstaller-сборка: __file__ указывает на временную папку
    # распаковки (пропадает после закрытия) — smety.sqlite должна
    # резолвиться рядом с exe (sys.executable), а не рядом с __file__,
    # иначе сметы руководителя терялись бы при каждом перезапуске
    # (см. записи разработки).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\BudgetPIR\BudgetPIR.exe")

    path = _default_db_path()

    assert path == Path(r"C:\Program Files\BudgetPIR\data\smety.sqlite")


def test_default_db_path_resolves_relative_to_file_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    path = _default_db_path()

    assert path.name == "smety.sqlite"
    assert path.parent.name == "data"
    assert not getattr(sys, "frozen", False)


def test_save_and_load_smeta_roundtrips_module_code_and_input_data(tmp_path):
    store = make_store(tmp_path)
    input_data = {"drilling_length": 450, "soil_category": "IV", "drilling_count": 30}

    smeta_id = store.save_smeta("Объект №1", "geology", input_data)
    module_code, loaded_input_data, edits = store.load_smeta(smeta_id)

    assert module_code == "geology"
    assert loaded_input_data == input_data
    assert edits == []


def test_save_edits_and_load_smeta_restores_edits_in_order(tmp_path):
    store = make_store(tmp_path)
    smeta_id = store.save_smeta("Объект №1", "geology", {"a": 1})

    edits = [
        Edit("geology.drilling.IV", "qty", 450.0, 500.0, reason="уточнение по факту"),
        Edit("geology.transport", "price", 0.0, 10.0, reason=None),
    ]
    store.save_edits(smeta_id, edits)

    _, _, loaded_edits = store.load_smeta(smeta_id)

    assert loaded_edits == edits


def test_save_edits_overwrites_previous_edits_entirely(tmp_path):
    store = make_store(tmp_path)
    smeta_id = store.save_smeta("Объект №1", "geology", {"a": 1})

    store.save_edits(smeta_id, [Edit("a", "qty", 1.0, 2.0)])
    store.save_edits(smeta_id, [Edit("b", "price", 3.0, 4.0)])

    _, _, loaded_edits = store.load_smeta(smeta_id)

    assert loaded_edits == [Edit("b", "price", 3.0, 4.0)]


def test_save_smeta_does_not_overwrite_existing_records(tmp_path):
    store = make_store(tmp_path)

    first_id = store.save_smeta("Объект №1", "geology", {"a": 1})
    second_id = store.save_smeta("Объект №1", "geology", {"a": 2})

    assert first_id != second_id
    assert store.load_smeta(first_id)[1] == {"a": 1}
    assert store.load_smeta(second_id)[1] == {"a": 2}


def test_list_smety_returns_several_saved_smety(tmp_path):
    store = make_store(tmp_path)

    id_1 = store.save_smeta("Объект №1", "geology", {"a": 1})
    id_2 = store.save_smeta("Объект №2", "geophysics", {"b": 2})

    listed = store.list_smety()
    listed_ids = {row[0] for row in listed}

    assert listed_ids == {id_1, id_2}
    by_id = {row[0]: row for row in listed}
    assert by_id[id_1][1] == "Объект №1"
    assert by_id[id_1][2] == "geology"
    assert by_id[id_2][1] == "Объект №2"
    assert by_id[id_2][2] == "geophysics"


def test_load_smeta_missing_id_raises_key_error(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(KeyError):
        store.load_smeta(999)


# --- несколько экземпляров программы одновременно: WAL + повтор при "database is locked" ---


def _hold_write_lock(db_path: Path) -> sqlite3.Connection:
    """Открывает ОТДЕЛЬНОЕ соединение к той же базе и держит открытой
    write-транзакцию (BEGIN IMMEDIATE, без commit/rollback) — имитирует
    второй экземпляр программы, который в этот момент сам что-то
    пишет. Пока вызывающий тест не вызовет `.commit()`/`.rollback()`/
    `.close()` на возвращённом соединении, ЛЮБАЯ другая попытка записи
    в эту же базу должна получать `sqlite3.OperationalError`
    ("database is locked")."""
    blocker = sqlite3.connect(db_path, timeout=0)
    blocker.execute("PRAGMA journal_mode=WAL")
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute(
        "INSERT INTO smety (name, module_code, input_data, created_at) "
        "VALUES ('blocker', 'geology', '{}', 'now')"
    )
    return blocker


def test_journal_mode_is_wal(tmp_path):
    store = make_store(tmp_path)
    conn = store._connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_save_smeta_retries_and_succeeds_once_lock_is_released(tmp_path, monkeypatch):
    # короткие задержки — тест не должен реально ждать секунды
    monkeypatch.setattr(persistence_module, "_RETRY_BASE_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(persistence_module, "_CONNECTION_TIMEOUT_SECONDS", 0.05)

    store = make_store(tmp_path)
    blocker = _hold_write_lock(store._db_path)

    result: dict = {}

    def writer():
        result["id"] = store.save_smeta("Concurrent", "geology", {"a": 1})

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.15)  # даём повтору хотя бы раз упереться в блокировку
    blocker.commit()
    blocker.close()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert "id" in result
    module_code, input_data, _ = store.load_smeta(result["id"])
    assert module_code == "geology"
    assert input_data == {"a": 1}


def test_save_smeta_raises_busy_error_if_lock_never_released(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_MAX_WRITE_RETRIES", 2)
    monkeypatch.setattr(persistence_module, "_RETRY_BASE_DELAY_SECONDS", 0.02)
    monkeypatch.setattr(persistence_module, "_CONNECTION_TIMEOUT_SECONDS", 0.02)

    store = make_store(tmp_path)
    blocker = _hold_write_lock(store._db_path)

    try:
        with pytest.raises(SmetaStoreBusyError):
            store.save_smeta("Never released", "geology", {})
    finally:
        blocker.rollback()
        blocker.close()


def test_save_edits_also_retries_on_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_RETRY_BASE_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(persistence_module, "_CONNECTION_TIMEOUT_SECONDS", 0.05)

    store = make_store(tmp_path)
    smeta_id = store.save_smeta("For edits", "geology", {})
    edits = [Edit("a", "qty", 1.0, 2.0)]

    blocker = _hold_write_lock(store._db_path)

    result: dict = {}

    def writer():
        store.save_edits(smeta_id, edits)
        result["done"] = True

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.15)
    blocker.commit()
    blocker.close()
    thread.join(timeout=5)

    assert result.get("done") is True
    _, _, loaded_edits = store.load_smeta(smeta_id)
    assert loaded_edits == edits


def test_concurrent_construction_against_new_db_does_not_crash(tmp_path, monkeypatch):
    # Найдено при живой проверке двух exe-экземпляров, запущенных
    # почти одновременно (см. записи разработки): каждый `SmetaStore(...)` сам вызывает `_create_tables()`
    # в конструкторе — если файла базы ещё не существует и НЕСКОЛЬКО
    # экземпляров конструируют `SmetaStore` на ту же ещё-не-созданную
    # базу практически одновременно, `_create_tables()` без защиты
    # `_run_write` падала необработанным `sqlite3.OperationalError`
    # ("database is locked") ПРЯМО В КОНСТРУКТОРЕ — теряя все 10
    # последующих сохранений одного из потоков целиком.
    monkeypatch.setattr(persistence_module, "_RETRY_BASE_DELAY_SECONDS", 0.02)
    monkeypatch.setattr(persistence_module, "_CONNECTION_TIMEOUT_SECONDS", 0.02)

    db_path = tmp_path / "fresh_concurrent.sqlite"
    errors: list[Exception] = []
    stores: list[SmetaStore] = []

    def construct():
        try:
            stores.append(SmetaStore(db_path))
        except Exception as exc:  # noqa: BLE001 — фиксируем любую поломку
            errors.append(exc)

    threads = [threading.Thread(target=construct) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert len(stores) == 5

    smeta_id = stores[0].save_smeta("После гонки конструкторов", "geology", {"a": 1})
    _, loaded_input_data, _ = stores[-1].load_smeta(smeta_id)
    assert loaded_input_data == {"a": 1}


def test_run_write_retries_when_connect_itself_is_locked(tmp_path, monkeypatch):
    # Целенаправленно воспроизводит именно ту гонку, что осталась
    # необработанной после первого прохода фикса: `_connect()` (сам
    # `PRAGMA journal_mode=WAL`) МОЖЕТ бросить "database is locked", а
    # раньше вызывался вне try/except внутри `_run_write` — повтор
    # его не ловил вообще, падение было необработанным. Мокаем
    # `_connect`, а не реальный файл — так тест детерминирован (не
    # зависит от того, успеет ли реальный PRAGMA словить блокировку).
    monkeypatch.setattr(persistence_module, "_RETRY_BASE_DELAY_SECONDS", 0.01)

    store = make_store(tmp_path)
    real_connect = store._connect
    calls = {"n": 0}

    def flaky_connect():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect()

    monkeypatch.setattr(store, "_connect", flaky_connect)

    smeta_id = store.save_smeta("После сбоя connect", "geology", {"a": 1})

    assert calls["n"] == 2
    _, loaded_input_data, _ = store.load_smeta(smeta_id)
    assert loaded_input_data == {"a": 1}


# --- цепочка версий многоэтапного расчёта (save_version) ---



def _volume_result(qty=10, based_on=None, stage_key="volumes"):
    lines = {"work": VolumeLine("work", "Работа", "шт", qty, SOURCE_INPUT, "ввод")}
    edits = [Edit("work", "qty", 9, qty, "по факту")]
    return make_result(stage_key, lines, {"n": qty}, edits, based_on or {})


def test_save_version_chain_links_each_version_to_its_parent(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    first = store.save_version("ГОК", "geology", None, _volume_result(10))
    second = store.save_version("ГОК", "geology", first, _volume_result(10, stage_key="estimate"))
    third = store.save_version("ГОК", "geology", second, _volume_result(12))

    module_code, name, chain = store.load_version_chain(third)
    assert (module_code, name) == ("geology", "ГОК")
    assert [r.stage_key for r in chain] == ["volumes", "estimate", "volumes"]
    rows = {row[0]: row for row in store.list_versions("geology")}
    assert rows[first][1] is None
    assert rows[second][1] == first
    assert rows[third][1] == second


def test_load_version_chain_restores_snapshot_exactly(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    original = _volume_result(7.5, based_on={"x": "abc"})
    version_id = store.save_version("ГОК", "geology", None, original)

    _code, _name, [restored] = store.load_version_chain(version_id)
    assert dict(restored.lines) == dict(original.lines)
    assert dict(restored.inputs) == {"n": 7.5}
    assert restored.edits == original.edits
    assert restored.fingerprint == original.fingerprint
    assert restored.confirmed_at == original.confirmed_at
    assert dict(restored.based_on) == {"x": "abc"}


def test_versions_can_branch_from_the_same_parent(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    root = store.save_version("ГОК", "geology", None, _volume_result(10))
    a = store.save_version("ГОК", "geology", root, _volume_result(11))
    b = store.save_version("ГОК", "geology", root, _volume_result(12))

    assert [r.lines["work"].qty for r in store.load_version_chain(a)[2]] == [10, 11]
    assert [r.lines["work"].qty for r in store.load_version_chain(b)[2]] == [10, 12]


def test_save_version_rejects_missing_or_foreign_parent(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    with pytest.raises(KeyError):
        store.save_version("ГОК", "geology", 999, _volume_result())
    other = store.save_version("Карьер", "ecology", None, _volume_result())
    with pytest.raises(ValueError):
        store.save_version("ГОК", "geology", other, _volume_result())
    assert len(store.list_versions()) == 1


def test_load_version_chain_missing_id_raises_key_error(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    with pytest.raises(KeyError):
        store.load_version_chain(1)


def test_versions_do_not_mix_with_single_stage_smety(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    store.save_smeta("Геофизика", "geophysics", {"vez_count": 1})
    store.save_version("ГОК", "geology", None, _volume_result())
    assert len(store.list_smety()) == 1
    assert len(store.list_versions()) == 1


# --- черновики (save_draft): отдельно от цепочки версий ---


def test_save_draft_overwrites_same_module_and_name(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    first = store.save_draft("ГОК", "geology", "volumes", {"drilling_count": 10}, [], None)
    second = store.save_draft(
        "ГОК", "geology", "estimate", {}, [Edit("geology.transport", "price", 0, 1, None)], None,
    )
    assert first == second
    assert len(store.list_drafts()) == 1
    draft = store.load_draft(first)
    assert (draft.stage_key, draft.inputs) == ("estimate", {})
    assert draft.edits == [Edit("geology.transport", "price", 0, 1, None)]


def test_drafts_are_separate_per_module_and_name(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    store.save_draft("ГОК", "geology", "volumes", {}, [], None)
    store.save_draft("Карьер", "geology", "volumes", {}, [], None)
    store.save_draft("ГОК", "ecology", "volumes", {}, [], None)
    assert len(store.list_drafts()) == 3


def test_draft_does_not_create_versions_and_keeps_base_version(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    version_id = store.save_version("ГОК", "geology", None, _volume_result())
    draft_id = store.save_draft("ГОК", "geology", "estimate", {}, [], version_id)
    assert len(store.list_versions()) == 1
    assert store.load_draft(draft_id).base_version_id == version_id


def test_save_draft_rejects_missing_or_foreign_base_version(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    with pytest.raises(KeyError):
        store.save_draft("ГОК", "geology", "volumes", {}, [], 999)
    other = store.save_version("Карьер", "ecology", None, _volume_result())
    with pytest.raises(ValueError):
        store.save_draft("ГОК", "geology", "volumes", {}, [], other)
    assert store.list_drafts() == []


def test_load_draft_missing_id_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        SmetaStore(tmp_path / "smety.sqlite").load_draft(1)
