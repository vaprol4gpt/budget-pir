"""Версия схемы базы смет, проверка связей и версия формата снимков
(Э4 дорожной карты, 25.10.2026)."""

import json
import sqlite3

import pytest

from core.persistence import SCHEMA_VERSION, SmetaStore, SmetaStoreFormatError
from core.stages import (
    LINE_FORMAT_VERSION,
    SOURCE_INPUT,
    SnapshotFormatError,
    VolumeLine,
    lines_from_json,
    lines_to_json,
    make_result,
)


def _result():
    lines = {"w": VolumeLine("w", "Работа", "шт", 1, SOURCE_INPUT, "ввод")}
    return make_result("volumes", lines, {"n": 1}, [], {})


def test_foreign_keys_enabled(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    conn = store._connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_foreign_key_violation_is_rejected(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    conn = store._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO smeta_versions (parent_id, name, module_code, stage_key, inputs, edits, "
                "lines, fingerprint, based_on, confirmed_at) VALUES "
                "(999, 'x', 'geology', 'volumes', '{}', '[]', '[]', 'f', '{}', 't')"
            )
    finally:
        conn.close()


def test_new_database_has_current_schema_version(tmp_path):
    path = tmp_path / "smety.sqlite"
    SmetaStore(path)
    SmetaStore(path)  # повторное открытие не дублирует запись
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT version FROM schema_version").fetchall() == [(SCHEMA_VERSION,)]
    conn.close()


def test_database_from_newer_program_is_refused_and_untouched(tmp_path):
    path = tmp_path / "smety.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(SmetaStoreFormatError, match="более новой версией"):
        SmetaStore(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT version FROM schema_version").fetchall() == [(SCHEMA_VERSION + 1,)]
    conn.close()


def test_lines_json_carries_format_version():
    items = lines_to_json(_result().lines)
    assert items[0]["v"] == LINE_FORMAT_VERSION


def test_lines_without_version_are_format_1():
    items = lines_to_json(_result().lines)
    items[0].pop("v")
    assert lines_from_json(items)["w"].qty == 1


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda item: item.update(v=LINE_FORMAT_VERSION + 1), "формате v"),
        (lambda item: item.update(type="FutureLine"), "неизвестный тип"),
        (lambda item: item["data"].update(new_field=1), "не знакомы"),
        (lambda item: item["data"].pop("qty"), "не хватает полей"),
    ],
)
def test_bad_snapshot_line_raises_clear_error(mutate, message):
    items = lines_to_json(_result().lines)
    mutate(items[0])
    with pytest.raises(SnapshotFormatError, match=message):
        lines_from_json(items)


def test_load_version_chain_with_bad_snapshot_names_version(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    version_id = store.save_version("ГОК", "geology", None, _result())
    conn = sqlite3.connect(tmp_path / "smety.sqlite")
    items = json.loads(conn.execute("SELECT lines FROM smeta_versions").fetchone()[0])
    items[0]["v"] = 99
    conn.execute("UPDATE smeta_versions SET lines = ?", (json.dumps(items),))
    conn.commit()
    conn.close()
    with pytest.raises(SnapshotFormatError, match=f"Версия #{version_id}"):
        store.load_version_chain(version_id)


# --- аудит 05.11.2026, Н7–Н9: повреждённый файл, порядок проверки версии, копия перед обновлением ---


def test_damaged_file_gives_a_clear_error_and_stays_untouched(tmp_path):
    from core.persistence import SmetaStore, SmetaStoreFormatError

    path = tmp_path / "smety.sqlite"
    path.write_bytes(b"this is not a database at all" * 100)
    before = path.read_bytes()
    with pytest.raises(SmetaStoreFormatError, match="повреждён"):
        SmetaStore(path)
    assert path.read_bytes() == before


def test_newer_database_is_refused_before_any_table_is_created(tmp_path):
    import sqlite3

    from core.persistence import SCHEMA_VERSION, SmetaStore, SmetaStoreFormatError

    path = tmp_path / "smety.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(SmetaStoreFormatError, match="более новой версией"):
        SmetaStore(path)
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert tables == {"schema_version"}


def test_backup_is_made_before_schema_upgrade(tmp_path):
    import sqlite3

    from core.persistence import SCHEMA_VERSION, SmetaStore

    path = tmp_path / "smety.sqlite"
    SmetaStore(path)  # новая база
    assert not list(tmp_path.glob("*до_схемы*"))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION - 1,))
    conn.commit()
    conn.close()
    SmetaStore(path)
    backups = list(tmp_path.glob("*до_схемы*"))
    assert [b.suffix for b in backups] == [".sqlite"]  # под data/*.sqlite в .gitignore
    conn = sqlite3.connect(backups[0])
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION - 1
    conn.close()


def test_edit_journal_from_newer_program_has_a_clear_message():
    from core.persistence import _edits_from_json
    from core.stages import SnapshotFormatError

    raw = '[{"line_id": "a", "field": "qty", "old_value": 1, "new_value": 2, "future": 1}]'
    with pytest.raises(SnapshotFormatError, match="более новой версией"):
        _edits_from_json(raw)
