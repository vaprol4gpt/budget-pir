"""Общие настройки тестов.

Локальный слой норм компании (`data/local/norms_firm.json`, С4 30.10.2026) есть
только на ПК, где его заполнили, и в git не хранится. Чтобы тесты давали
одинаковый результат на любом ПК, по умолчанию он отключён: `LOCAL_DIR`
указывает на пустую папку. Тесты самого локального слоя передают папку явно.
"""

import pytest

import core.norms


@pytest.fixture(autouse=True)
def no_local_norms(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(core.norms, "LOCAL_DIR", tmp_path_factory.mktemp("no_local_norms"))
