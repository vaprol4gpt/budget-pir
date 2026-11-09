"""Справочник норм для расчёта себестоимости (минимального бюджета) — Э6
дорожной карты, 25.10.2026.

Нормы хранятся НЕ в `data/references.sqlite` (тот пересобирается из
эталонных смет парсером и в git не хранится), а в JSON-файлах
`data/norms/<вид работ>.json` под git: это значения, введённые вручную,
их происхождение должно быть видно в истории. У каждой записи — значение,
единица, источник и пометка допущения; пользователь заменяет значения
правкой файла (или полем ввода в интерфейсе для тех, что вынесены в
форму)."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

NORMS_DIR = Path(__file__).resolve().parent.parent / "data" / "norms"
NORMS_FORMAT_VERSION = 1

# Локальный слой (С4, 30.10.2026): внутренние ставки компании текущего года
# (оклады, суточные, проживание…) из исходников заказчика — В GIT НЕ КЛАДУТСЯ
# (`data/local/` в .gitignore). Файл `data/local/norms_firm.json`, формат —
# шаблон `data/norms/norms_firm.example.json`. Значение локального слоя
# перекрывает seed-допущение из `data/norms/<вид>.json`; у нормы видно, откуда
# она («локально: …» / «допущение …»). Нет файла — работают seed-значения.
#
# Признак «ставка от бухгалтерии» (03.11.2026, для статуса калибровки
# минимума — `modules/geology/cost.py::rates_status`): у записи локального
# слоя (и в "inputs", и в "norms") — НЕОБЯЗАТЕЛЬНЫЕ поля
#   "kind": "accounting" — ставка от бухгалтерии компании (штатное расписание,
#           фактические затраты; её ставит импорт шаблона бухгалтерии
#           `tools/import_stavki.py`);
#           "price" — ставка из ценового документа (Форма 2.2 «Бюджетного
#           расчёта» и т.п.: цена, а не себестоимость);
#           нет поля / пусто — происхождение не указано (считается НЕ от
#           бухгалтерии — так прочитаются все файлы, заполненные до 03.11.2026);
#   "date": "ГГГГ-ММ-ДД" — дата, на которую бухгалтерия дала ставку (или дата
#           выгрузки шаблона); показывается рядом с источником.
# Формат файла при этом не меняется (`_формат` 1): старые файлы читаются как раньше.
KIND_ACCOUNTING = "accounting"
KIND_PRICE = "price"

LOCAL_DIR = Path(__file__).resolve().parent.parent / "data" / "local"
LOCAL_FILE = "norms_firm.json"

ORIGIN_SEED = "seed"
ORIGIN_LOCAL = "local"


@dataclass(frozen=True)
class Norm:
    key: str
    value: float | None
    unit: str
    source: str
    assumption: bool
    note: str = ""
    origin: str = ORIGIN_SEED
    kind: str = ""  # только у локального слоя: KIND_ACCOUNTING / KIND_PRICE / ""
    date: str = ""  # только у локального слоя: дата ставки


@dataclass(frozen=True)
class LocalValue:
    """Значение поля ввода по умолчанию из локального слоя и его источник."""

    value: float
    source: str
    kind: str = ""  # KIND_ACCOUNTING / KIND_PRICE / "" (не указано)
    date: str = ""


def _local_section(name: str, local_dir: Path | None) -> dict:
    path = (local_dir if local_dir is not None else LOCAL_DIR) / LOCAL_FILE
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("_формат", 1) > NORMS_FORMAT_VERSION:
        raise NormsFormatError(f"{path}: формат v{raw.get('_формат')} новее поддерживаемого")
    return raw.get(name, {})


def local_input_defaults(name: str, local_dir: Path | None = None) -> dict[str, LocalValue]:
    """Значения полей ввода по умолчанию из локального слоя (оклады и т.п.):
    поле → (значение, источник). Пустые (null) — пропускаются."""
    result = {}
    for key, item in _local_section(name, local_dir).get("inputs", {}).items():
        if item.get("value") is None:
            continue
        if not item.get("source"):
            raise NormsFormatError(f"локальный слой: у поля {key!r} нет источника")
        result[key] = LocalValue(float(item["value"]), item["source"],
                                 str(item.get("kind") or ""), str(item.get("date") or ""))
    return result


class NormsFormatError(ValueError):
    """Файл норм не читается: формат новее программы или запись без
    обязательных полей."""


def load_norms(
    name: str, norms_dir: Path | None = None, local_dir: Path | None = None, use_local: bool = True
) -> dict[str, Norm]:
    """Нормы из `data/norms/<name>.json` по ключу; значения локального слоя
    (`use_local`, папка `local_dir`, по умолчанию `LOCAL_DIR`) перекрывают
    seed-допущения — норма получает `origin="local"` и источник «локально: …»."""
    result = _load_seed(name, norms_dir)
    if not use_local:
        return result
    for key, item in _local_section(name, local_dir).get("norms", {}).items():
        if item.get("value") is None or key not in result:
            continue
        if not item.get("source"):
            raise NormsFormatError(f"локальный слой: у нормы {key!r} нет источника")
        result[key] = dataclasses.replace(
            result[key], value=item["value"], source=f"локально: {item['source']}",
            assumption=bool(item.get("assumption", False)), note=item.get("note", ""),
            origin=ORIGIN_LOCAL, kind=str(item.get("kind") or ""), date=str(item.get("date") or ""),
        )
    return result


def _load_seed(name: str, norms_dir: Path | None) -> dict[str, Norm]:
    """Нормы из `data/norms/<name>.json` по ключу."""
    path = (norms_dir or NORMS_DIR) / f"{name}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("_формат", 1) > NORMS_FORMAT_VERSION:
        raise NormsFormatError(f"{path}: формат v{raw.get('_формат')} новее поддерживаемого")
    result = {}
    for key, item in raw.get("norms", {}).items():
        missing = {"value", "unit", "source", "assumption"} - set(item)
        if missing:
            raise NormsFormatError(f"{path}: у нормы {key!r} нет полей {sorted(missing)}")
        result[key] = Norm(key, item["value"], item["unit"], item["source"], bool(item["assumption"]),
                           item.get("note", ""))
    return result
