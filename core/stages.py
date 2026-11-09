"""Многоэтапный сценарий модуля (вариант B из docs/multistage_design.md).

Модуль — упорядоченный список этапов (`Stage`). Каждый этап — это,
по сути, мини-версия `BudgetModule`: свои поля ввода, свой расчёт,
свой результат (словарь строк-dataclass-ов: `VolumeLine` для
ведомости, `EstimateLine` для сметы). Этап K считается от
ПОДТВЕРЖДЁННЫХ снимков этапов 1..K-1, а не от их текущих черновиков.

Старый однoэтапный модуль (только `BudgetModule`: `build_volumes` +
`build_estimate`) — частный случай: `as_staged()` оборачивает его в
`StagedModule` с одним этапом, модуль при этом не меняется.

Правки оператора — свой `OverrideLog` на каждый этап
(`core/overrides.py` не менялся: он и так применяет правки через
`dataclasses.replace` к любому dataclass-у строки).

Подтверждение — неизменяемый снимок (`StageResult`) + отпечаток
содержимого. Снимок этапа K помнит отпечатки снимков, от которых он
посчитан (`based_on`); если более ранний этап открыт на доработку или
переподтверждён с другим содержимым — снимок K "устарел" (`is_stale`),
но не удаляется.

Принятые решения по открытым вопросам — docs/multistage_design.md,
раздел "Принятые решения"."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Mapping

from core.models import EstimateLine
from core.module_base import Field
from core.overrides import CUSTOM_FIELDS, RATE_KINDS, Edit, OverrideLog, is_custom_id
from core.validation import negative_quantity_warnings

# Происхождение значения строки ведомости (`VolumeLine.source`).
# Классы — из docs/predmetnaya_oblast.md. "Накопленная информация"
# (история сохранённых смет) как источник пока не реализована — для неё
# появится своё значение, когда дойдёт очередь.
SOURCE_INPUT = "input"
SOURCE_CLASS_A = "class_a"
SOURCE_CLASS_B = "class_b"
SOURCE_CLASS_V = "class_v"
SOURCE_LAB = "lab_profile"
SOURCE_OPERATOR = "operator"

SOURCE_LABELS = {
    SOURCE_INPUT: "ввод оператора",
    SOURCE_CLASS_A: "авто, класс А",
    SOURCE_CLASS_B: "авто, класс Б",
    SOURCE_CLASS_V: "типовой профиль (класс В)",
    SOURCE_LAB: "лабораторный профиль",
    SOURCE_OPERATOR: "правка оператора",
}

# Статусы этапа в сессии.
STATUS_DRAFT = "draft"
STATUS_CONFIRMED = "confirmed"
STATUS_STALE = "stale"

STATUS_LABELS = {
    STATUS_DRAFT: "черновик",
    STATUS_CONFIRMED: "подтверждён",
    STATUS_STALE: "устарел",
}


@dataclass(frozen=True)
class VolumeLine:
    """Строка ведомости объёмов работ — то, ЧТО и СКОЛЬКО делать, без
    цен. `id` совпадает с ключом словаря `volumes`, который модуль
    передаёт в свой `build_estimate` — так ведомость остаётся тем же
    самым входом сметы, что и раньше, только теперь с названием,
    единицей и происхождением значения."""

    id: str
    name: str
    unit: str
    qty: float
    source: str
    basis: str = ""


# Типы строк, которые умеет сериализовать снимок (для SmetaStore).
LINE_TYPES: dict[str, type] = {"VolumeLine": VolumeLine, "EstimateLine": EstimateLine}

# Версия формата сериализованной строки (ключ "v" в JSON, Э4 дорожной
# карты, 25.10.2026). Строки без "v" — формат 1 (до введения ключа, та же
# структура). Новое необязательное поле dataclass-а с умолчанием (как
# EstimateLine.rounding) версию НЕ поднимает: старые данные читаются и так.
# Поднимать — при несовместимом изменении (переименование/удаление поля) и
# добавлять миграцию в lines_from_json.
LINE_FORMAT_VERSION = 1


class SnapshotFormatError(ValueError):
    """Сохранённый снимок нельзя прочитать этой версией программы:
    неизвестный тип строки, формат новее поддерживаемого или поля строки
    не совпадают с текущим dataclass-ом. Интерфейс показывает сообщение,
    а не падает."""

# compute(inputs, previous, refs, pinned) -> dict[str, line]
#   inputs   — ввод оператора ЭТОГО этапа (dict по Field.name)
#   previous — подтверждённые снимки более ранних этапов, по Stage.key
#   pinned   — правки оператора ЭТОГО этапа, {line_id: {field: value}}
#              (последняя правка поля побеждает). Нужны расчёту, чтобы
#              зависимые позиции считались от ЗАКРЕПЛЁННОГО значения
#              позиции-источника (решение №6). Этап, которому это не
#              нужно, просто игнорирует аргумент — правки всё равно
#              накладываются поверх результата сессией.
ComputeFn = Callable[[dict, Mapping[str, "StageResult"], Any, dict[str, dict[str, Any]]], dict]
CheckFn = Callable[[dict], list[str]]
# context_warnings(inputs, previous, refs) -> предупреждения этапа, которые
# зависят не от строк результата, а от контекста расчёта (данные объекта в
# refs, core/object_data.py) — check(lines) их не видит.
ContextWarningsFn = Callable[[dict, Mapping[str, "StageResult"], Any], list[str]]
# editable_fields(line, previous) -> какие поля строки оператор может править
EditableFn = Callable[[Any, Mapping[str, "StageResult"]], tuple[str, ...]]

DEFAULT_EDITABLE_FIELDS = ("qty", "price")
# Строка «доля от базы» (kind B/C): в сумме участвует только ставка.
RATE_EDITABLE_FIELDS = ("rate",)


def default_editable_fields(line: Any) -> tuple[str, ...]:
    """Правимые поля строки без правил этапа: у строки kind B/C —
    ставка, у остальных — объём и цена."""
    if getattr(line, "kind", None) in RATE_KINDS:
        return RATE_EDITABLE_FIELDS
    return DEFAULT_EDITABLE_FIELDS


def _default_editable(line: Any, previous: Mapping[str, "StageResult"]) -> tuple[str, ...]:
    return default_editable_fields(line)


def _no_warnings(lines: dict) -> list[str]:
    return []


@dataclass
class Stage:
    number: int
    key: str
    title: str
    fields: list[Field]
    compute: ComputeFn
    check: CheckFn = _no_warnings
    editable_fields: EditableFn = _default_editable
    # Подписи кнопок/заголовков в интерфейсе.
    confirm_label: str = "Подтвердить"
    # Необязательные предупреждения по контексту расчёта (см. ContextWarningsFn).
    context_warnings: ContextWarningsFn | None = None


@dataclass(frozen=True)
class StageResult:
    """Неизменяемый снимок подтверждённого этапа.

    `lines` — эффективный результат этапа (с правками оператора) на
    момент подтверждения, только для чтения (`MappingProxyType` поверх
    глубокой копии — изменение исходных строк после подтверждения
    снимок не затрагивает). `inputs`/`edits` — из чего снимок получен
    (нужно, чтобы восстановить форму и журнал правок при открытии
    сохранённой версии). `fingerprint` — отпечаток содержимого `lines`;
    `based_on` — отпечатки снимков более ранних этапов, от которых
    посчитан этот (`{stage_key: fingerprint}`).

    `is_stale` — не часть содержимого снимка, а его статус в сессии:
    `StagedSession.result()` отдаёт копию с актуальным значением
    флага (`dataclasses.replace`), сам снимок не меняется."""

    stage_key: str
    lines: Mapping[str, Any]
    inputs: Mapping[str, Any]
    edits: tuple[Edit, ...]
    confirmed_at: str
    fingerprint: str
    based_on: Mapping[str, str]
    is_stale: bool = False

    def values(self) -> dict[str, float]:
        """`{id строки: qty}` — форма, в которой ведомость передаётся
        в `build_estimate` модуля."""
        return {line_id: line.qty for line_id, line in self.lines.items()}


@dataclass
class StageDraft:
    """Текущий (не подтверждённый) результат этапа."""

    base: dict[str, Any]
    effective: dict[str, Any]
    not_applied: list[Edit]
    warnings: list[str]


class StageNotReady(RuntimeError):
    """Этап нельзя посчитать/подтвердить: более ранний этап не
    подтверждён или устарел."""


@dataclass
class StagedModule:
    """Модуль как список этапов. `module` — исходный объект модуля
    (нужен интерфейсу для `field_options`)."""

    module: Any
    stages: list[Stage]

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("У модуля должен быть хотя бы один этап")
        numbers = [stage.number for stage in self.stages]
        if numbers != list(range(1, len(self.stages) + 1)):
            raise ValueError(f"Номера этапов должны идти 1..N по порядку, получено {numbers}")
        keys = [stage.key for stage in self.stages]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Ключи этапов должны быть уникальны, получено {keys}")

    @property
    def code(self) -> str:
        return self.module.code

    @property
    def is_single_stage(self) -> bool:
        return len(self.stages) == 1

    def stage(self, key: str) -> Stage:
        for stage in self.stages:
            if stage.key == key:
                return stage
        raise KeyError(f"Этап '{key}' не найден в модуле '{self.code}'")

    def index(self, key: str) -> int:
        return self.stages.index(self.stage(key))


def as_staged(module: Any) -> StagedModule:
    """Модуль с методом `stages()` — как есть; старый `BudgetModule` —
    через адаптер: один этап = вся прежняя логика (`build_volumes` →
    `build_estimate`, предупреждения — `check`)."""
    if hasattr(module, "stages"):
        return StagedModule(module, module.stages())

    def compute(inputs, previous, refs, pinned):
        return module.build_estimate(module.build_volumes(inputs, refs=refs), refs=refs)

    return StagedModule(
        module,
        [
            Stage(
                number=1,
                key="estimate",
                title="Смета",
                fields=module.input_schema(),
                compute=compute,
                check=module.check,
                confirm_label="Подтвердить смету",
            )
        ],
    )


def _field_value(line: Any, field_name: str) -> float | None:
    """Текущее значение правимого поля строки (`rate` — из coefs)."""
    if field_name == "rate":
        value = getattr(line, "coefs", {}).get("rate")
    else:
        value = getattr(line, field_name, None)
    return float(value) if isinstance(value, (int, float)) else None


def _close(a: float, b: Any) -> bool:
    return isinstance(b, (int, float)) and abs(a - b) <= 1e-9 * max(1.0, abs(a))


def pinned_values(overrides: OverrideLog) -> dict[str, dict[str, Any]]:
    """Правки журнала в виде `{line_id: {field: value}}`, последняя
    правка поля побеждает — тот же порядок, что в `effective_estimate`."""
    pinned: dict[str, dict[str, Any]] = {}
    for edit in overrides.history():
        if edit.field in CUSTOM_FIELDS:  # своя строка — не правка позиции модуля
            continue
        pinned.setdefault(edit.line_id, {})[edit.field] = edit.new_value
    return pinned


# Необязательные поля строки, которые НЕ пишутся в JSON снимка и не входят
# в отпечаток, пока равны умолчанию (Э7: `EstimateLine.placement`). Так
# снимки без своих строк остаются байт-в-байт прежними: их отпечаток не
# меняется (подтверждённая раньше смета не становится «отличается от
# снимка»), а прежняя версия программы их по-прежнему читает.
_OMIT_WHEN_DEFAULT = {"EstimateLine": {"placement": None}}


def line_to_dict(line: Any) -> dict:
    """Поля строки для JSON и отпечатка (см. `_OMIT_WHEN_DEFAULT`)."""
    data = dataclasses.asdict(line)
    for name, default in _OMIT_WHEN_DEFAULT.get(type(line).__name__, {}).items():
        if name in data and data[name] == default:
            del data[name]
    return data


def fingerprint(lines: Mapping[str, Any]) -> str:
    """Отпечаток содержимого строк (порядок ключей не важен)."""
    payload = [
        [line_id, type(line).__name__, line_to_dict(line)]
        for line_id, line in sorted(lines.items())
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def numbers_fingerprint(lines: Mapping[str, Any]) -> str:
    """Отпечаток строк без текста основания (`basis`): совпадает, когда расчёт
    тот же и поменялась только подпись основания (новая версия программы
    уточнила текст). Считается заново из строк снимка — хранимый `fingerprint`
    и формат снимка не меняются. Для решения «смета устарела — в бюджет не
    берём» (итоговое ревью прогона 30.10.2026, находка №1)."""
    payload = [
        [line_id, type(line).__name__,
         {k: v for k, v in line_to_dict(line).items() if k != "basis"}]
        for line_id, line in sorted(lines.items())
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def lines_to_json(lines: Mapping[str, Any]) -> list[dict]:
    return [
        {"type": type(line).__name__, "v": LINE_FORMAT_VERSION, "data": line_to_dict(line)}
        for line in lines.values()
    ]


def lines_from_json(items: list[dict]) -> dict[str, Any]:
    """Строки снимка из JSON. Ошибка формата — `SnapshotFormatError` с
    понятным текстом (какая строка и что не так), а не TypeError/KeyError."""
    result = {}
    for number, item in enumerate(items, start=1):
        version = item.get("v", 1)
        if not isinstance(version, int) or version > LINE_FORMAT_VERSION:
            raise SnapshotFormatError(
                f"Строка {number} снимка сохранена в формате v{version}, эта версия программы "
                f"читает до v{LINE_FORMAT_VERSION} — откройте её более новой версией программы"
            )
        line_type = LINE_TYPES.get(item.get("type"))
        if line_type is None:
            raise SnapshotFormatError(f"Строка {number} снимка: неизвестный тип {item.get('type')!r}")
        data = item.get("data")
        known = {f.name for f in dataclasses.fields(line_type)}
        if not isinstance(data, dict) or set(data) - known:
            extra = sorted(set(data) - known) if isinstance(data, dict) else "нет данных"
            raise SnapshotFormatError(
                f"Строка {number} снимка ({line_type.__name__}): поля {extra} не знакомы этой "
                "версии программы"
            )
        try:
            line = line_type(**data)
        except TypeError as exc:
            raise SnapshotFormatError(
                f"Строка {number} снимка ({line_type.__name__}): не хватает полей — {exc}"
            ) from exc
        result[line.id] = line
    return result


def _freeze(lines: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(copy.deepcopy(dict(lines)))


def make_result(
    stage_key: str,
    lines: Mapping[str, Any],
    inputs: Mapping[str, Any],
    edits: list[Edit] | tuple[Edit, ...],
    based_on: Mapping[str, str],
    confirmed_at: str | None = None,
) -> StageResult:
    return StageResult(
        stage_key=stage_key,
        lines=_freeze(lines),
        inputs=MappingProxyType(copy.deepcopy(dict(inputs))),
        edits=tuple(edits),
        confirmed_at=confirmed_at or datetime.now(timezone.utc).isoformat(),
        fingerprint=fingerprint(lines),
        based_on=MappingProxyType(dict(based_on)),
    )


@dataclass
class StagedSession:
    """Состояние расчёта одного модуля по этапам (одна на модуль в
    сессии браузера или в скрипте).

    `inputs[stage_key]` — ввод этапа, `overrides[stage_key]` — свой
    журнал правок на этап. `version_id` — id последней сохранённой
    версии в `SmetaStore` (родитель следующей), `None` — ещё ничего не
    сохранялось; сама сессия в базу не пишет."""

    staged: StagedModule
    inputs: dict[str, dict] = field(default_factory=dict)
    overrides: dict[str, OverrideLog] = field(default_factory=dict)
    current: str = ""
    version_id: int | None = None
    # Шифр объекта, с данными которого последний раз подтверждён этап или открыта
    # версия («» — не задан). Заполняет интерфейс; сводный бюджет по нему не даёт
    # сложить разделы разных объектов (core/aggregator.py::BudgetAggregator.mixed_objects).
    object_code: str = ""
    _snapshots: dict[str, StageResult] = field(default_factory=dict)
    _reopened: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        for stage in self.staged.stages:
            self.inputs.setdefault(stage.key, {})
            self.overrides.setdefault(stage.key, OverrideLog())
        if not self.current:
            self.current = self.staged.stages[0].key

    # --- статусы ---

    def _earlier(self, key: str) -> list[Stage]:
        return self.staged.stages[: self.staged.index(key)]

    def _is_stale(self, key: str) -> bool:
        snapshot = self._snapshots.get(key)
        if snapshot is None:
            return False
        for earlier in self._earlier(key):
            earlier_snapshot = self._snapshots.get(earlier.key)
            if earlier_snapshot is None or earlier.key in self._reopened:
                return True
            if self._is_stale(earlier.key):
                return True
            if snapshot.based_on.get(earlier.key) != earlier_snapshot.fingerprint:
                return True
        return False

    def status(self, key: str) -> str:
        if key not in self._snapshots or key in self._reopened:
            return STATUS_DRAFT
        if self._is_stale(key):
            return STATUS_STALE
        return STATUS_CONFIRMED

    def result(self, key: str) -> StageResult | None:
        """Последний снимок этапа (с актуальным `is_stale`) или `None`.
        Снимок этапа, открытого на доработку, по-прежнему возвращается
        (он не удаляется) — его статус при этом `draft`."""
        snapshot = self._snapshots.get(key)
        if snapshot is None:
            return None
        return dataclasses.replace(snapshot, is_stale=self._is_stale(key))

    def is_ready(self, key: str) -> bool:
        """Все более ранние этапы подтверждены и не устарели."""
        return all(self.status(stage.key) == STATUS_CONFIRMED for stage in self._earlier(key))

    def previous_results(self, key: str) -> dict[str, StageResult]:
        return {
            stage.key: self.result(stage.key)
            for stage in self._earlier(key)
            if stage.key in self._snapshots
        }

    def last_stage_status(self) -> str:
        return self.status(self.staged.stages[-1].key)

    # --- расчёт ---

    def accepts_custom_lines(self, key: str) -> bool:
        """Своя строка оператора (Э7, core/overrides.py) — только на
        последнем этапе (смета): у строки ведомости нет цены, а смета модуля
        строится по своим известным позициям ведомости и чужую не увидит."""
        return key == self.staged.stages[-1].key

    def editable_fields(self, key: str, line: Any) -> tuple[str, ...]:
        if is_custom_id(getattr(line, "id", "")):
            # Своя строка правится только целиком (OverrideLog.update_custom_line),
            # иначе правка поля прошла бы мимо её записи в журнале (ревью Э7, №7).
            return ()
        return self.staged.stage(key).editable_fields(line, self.previous_results(key))

    def compute(self, key: str, refs: Any) -> StageDraft:
        if not self.is_ready(key):
            raise StageNotReady(
                f"Этап «{self.staged.stage(key).title}» нельзя посчитать: "
                "более ранний этап не подтверждён или устарел"
            )
        stage = self.staged.stage(key)
        previous = self.previous_results(key)
        log = self.overrides[key]
        base = stage.compute(dict(self.inputs[key]), previous, refs, pinned_values(log))

        applicable_edits: list[Edit] = []
        not_applied: list[Edit] = []
        drifted: list[str] = []
        seen: set[tuple[str, str]] = set()
        custom_ok = self.accepts_custom_lines(key)
        for edit in log.history():
            # Своя строка (Э7) и правка её объёма/цены — не из расчёта модуля,
            # сверять не с чем; на этапе без своих строк — не применяется.
            if edit.field in CUSTOM_FIELDS or is_custom_id(edit.line_id):
                if custom_ok and edit.field in CUSTOM_FIELDS:
                    applicable_edits.append(edit)
                else:
                    not_applied.append(edit)
                continue
            line = base.get(edit.line_id)
            if line is None or edit.field not in stage.editable_fields(line, previous):
                not_applied.append(edit)
                continue
            applicable_edits.append(edit)
            # Первая правка поля помнит значение расчёта на момент правки;
            # если расчёт с тех пор дал другое (оператор поменял ввод формы,
            # расстояние, справочник) — правка всё равно побеждает, но молча
            # этого делать нельзя.
            if (edit.line_id, edit.field) in seen:
                continue
            seen.add((edit.line_id, edit.field))
            current = _field_value(line, edit.field)
            final = pinned_values(log)[edit.line_id][edit.field]
            # Этап, закрепляющий правки в самом расчёте (pinned, ведомость),
            # уже отдаёт значение правки — это не расхождение.
            if current is not None and not _close(current, edit.old_value) and not _close(current, final):
                drifted.append(
                    f"Правка «{getattr(line, 'name', edit.line_id)}» ({edit.field}): расчётное значение "
                    f"изменилось после правки ({edit.old_value:g} → {current:g}), применено значение "
                    f"правки {edit.new_value:g} — проверьте или отмените правку."
                )
        effective = OverrideLog.from_edits(applicable_edits).effective_estimate(base)
        warnings = drifted + stage.check(effective) + negative_quantity_warnings(effective)
        if stage.context_warnings is not None:
            warnings += stage.context_warnings(dict(self.inputs[key]), previous, refs)
        return StageDraft(base, effective, not_applied, warnings)

    # --- переходы ---

    def confirm(self, key: str, refs: Any, confirmed_at: str | None = None) -> StageResult:
        """Считает этап и фиксирует снимок. Переводит сессию на
        следующий этап (последний этап остаётся текущим)."""
        draft = self.compute(key, refs)
        based_on = {
            earlier_key: earlier.fingerprint
            for earlier_key, earlier in self.previous_results(key).items()
        }
        snapshot = make_result(
            key, draft.effective, self.inputs[key], self.overrides[key].history(),
            based_on, confirmed_at,
        )
        self._snapshots[key] = snapshot
        self._reopened.discard(key)
        index = self.staged.index(key)
        if index + 1 < len(self.staged.stages):
            self.current = self.staged.stages[index + 1].key
        return snapshot

    def reopen(self, key: str) -> None:
        """Возврат на этап для правки. Снимки этого и более поздних
        этапов НЕ удаляются: более поздние становятся устаревшими, пока
        этот этап не будет переподтверждён с тем же содержимым."""
        self.staged.stage(key)  # KeyError на неизвестный этап
        if key in self._snapshots:
            self._reopened.add(key)
        self.current = key

    def restore(self, results: list[StageResult]) -> None:
        """Восстановление подтверждённых снимков (открытие сохранённой
        версии). Последний снимок каждого этапа в списке побеждает;
        ввод и правки этапа берутся из его снимка. Текущий этап — первый
        не подтверждённый (или последний, если подтверждены все)."""
        self._snapshots.clear()
        self._reopened.clear()
        for result in results:
            self._snapshots[result.stage_key] = dataclasses.replace(result, is_stale=False)
            self.inputs[result.stage_key] = copy.deepcopy(dict(result.inputs))
            # from_edits, а не apply по полям — сохраняет данные своих строк.
            self.overrides[result.stage_key] = OverrideLog.from_edits(result.edits)
        self.current = next(
            (s.key for s in self.staged.stages if self.status(s.key) != STATUS_CONFIRMED),
            self.staged.stages[-1].key,
        )

    def apply_draft(self, stage_key: str, inputs: Mapping[str, Any], edits: list[Edit]) -> None:
        """Черновик поверх восстановленных версий (`restore`): ввод и
        правки этапа `stage_key` — из черновика, текущий этап — он же.

        Если у этапа уже есть подтверждённый снимок, а черновик от него
        отличается (ввод или правки) — этап открывается на доработку,
        как при `reopen`: более поздние этапы становятся устаревшими.
        Черновик, совпадающий со снимком, статус не меняет.

        Если более ранний этап не подтверждён (черновик сохранили, когда
        версия не записалась), текущим становится первый
        неподтверждённый этап — ввод черновика при этом остаётся у
        своего этапа и не теряется."""
        self.staged.stage(stage_key)  # KeyError на неизвестный этап
        self.inputs[stage_key] = copy.deepcopy(dict(inputs))
        self.overrides[stage_key] = OverrideLog.from_edits(edits)

        snapshot = self._snapshots.get(stage_key)
        if snapshot is not None and (
            dict(snapshot.inputs) != self.inputs[stage_key] or list(snapshot.edits) != list(edits)
        ):
            self._reopened.add(stage_key)

        if self.is_ready(stage_key):
            self.current = stage_key
        else:
            self.current = next(
                s.key for s in self.staged.stages if self.status(s.key) != STATUS_CONFIRMED
            )
