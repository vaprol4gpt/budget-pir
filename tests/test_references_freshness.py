"""Справочник сам пересобирается на других ПК (С2, 30.10.2026).

До 30.10.2026 после `git pull` на другом ПК старый справочник (без таблицы
ставок НДС) молча давал итог без НДС, а launch_app.bat пересобирал его
только при отсутствии файла. Теперь в справочнике — таблица meta (версия
формата, отпечаток исходников); при запуске программа и launch_app.bat
сверяют её и пересобирают; если не получилось — понятная ошибка."""

import sqlite3

import pytest

import data.build_references as br
from core.references import REFERENCES_SCHEMA_VERSION, ReferencesError, stale_reason
from data.build_references import PriceEntry, build_database

PRICE = PriceEntry("СБЦ-99", "9", "3", None, 47.2, "СБЦ-99 Таблица 9, п.3", "Рекогносцировка", 19)
needs_sources = pytest.mark.skipif(
    not br.SOURCE_XLSX.exists() or not br.ECOLOGY_SOURCE_XLSX.exists(),
    reason="нет эталонных смет в reference_data/",
)


def make_db(path, fingerprint="abc"):
    build_database([PRICE], [], path, sources_fingerprint=fingerprint)
    return path


def test_fresh_database_has_no_stale_reason(tmp_path):
    path = make_db(tmp_path / "r.sqlite")
    assert stale_reason(path, "abc") is None
    assert stale_reason(path) is None  # без отпечатка (exe) — только версия формата


def test_missing_file(tmp_path):
    assert "нет" in stale_reason(tmp_path / "none.sqlite")


def test_old_database_without_meta(tmp_path):
    path = make_db(tmp_path / "r.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE meta")
    conn.commit()
    conn.close()
    assert "старой версией" in stale_reason(path)


def test_other_schema_version(tmp_path):
    path = make_db(tmp_path / "r.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    assert f"нужна {REFERENCES_SCHEMA_VERSION}" in stale_reason(path)


def test_sources_changed(tmp_path):
    path = make_db(tmp_path / "r.sqlite", fingerprint="old")
    assert "изменились" in stale_reason(path, "new")


def test_corrupt_file(tmp_path):
    path = tmp_path / "r.sqlite"
    path.write_bytes(b"this is not a sqlite database at all" * 100)
    assert "повреждён" in stale_reason(path)


@needs_sources
def test_stale_database_is_rebuilt(tmp_path):
    """Устаревший справочник (старая версия программы, без meta) → пересборка
    из reference_data/, после неё — актуален."""
    path = make_db(tmp_path / "r.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE meta")
    conn.commit()
    conn.close()
    message = br.ensure_references(path)
    assert message is not None and "пересобран" in message and "meta" in message
    assert stale_reason(path, br.source_fingerprint()) is None
    assert br.ensure_references(path) is None  # второй раз — ничего не делает


@needs_sources
def test_corrupt_database_with_failed_rebuild_gives_clear_error(tmp_path, monkeypatch):
    path = tmp_path / "r.sqlite"
    path.write_bytes(b"garbage" * 1000)

    def broken_build(db_path=None):
        raise OSError("диск недоступен")

    monkeypatch.setattr(br, "build", broken_build)
    with pytest.raises(ReferencesError) as exc:
        br.ensure_references(path)
    text = str(exc.value)
    assert "повреждён" in text and "пересборка не удалась" in text and "диск недоступен" in text
    assert "build_references.py" in text  # что сделать вручную


def test_exe_mode_checks_version_without_sources(tmp_path, monkeypatch):
    """В exe исходников нет: справочник внутри программы сверяется по версии
    формата; повреждён — понятная ошибка «пересоберите программу»."""
    import app.ui as ui

    path = tmp_path / "r.sqlite"
    path.write_bytes(b"garbage" * 1000)
    monkeypatch.setattr(ui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ui, "REFERENCES_DB_PATH", path)
    with pytest.raises(ReferencesError) as exc:
        ui.prepare_references()
    assert "build_release.bat" in str(exc.value)
    make_db(path)
    assert ui.prepare_references() is None
