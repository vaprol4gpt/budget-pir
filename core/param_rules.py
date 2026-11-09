"""Справочник правил выбора параметров сметы (`data/pravila_parametrov.json`).

Зачем (прогон 03.11.2026, раздел 1.3): часть параметров сметы — коэффициенты листов,
районные, транспорт, неблагоприятный период — оператор раньше брал «из готовой сметы», а
правила их выбора знают только специалисты компании (вопросы — `docs/oprosnik.md`). Здесь эти
параметры собраны в одном файле данных: значение по умолчанию, значение в эталонном листе,
статус и источник. Модули берут значения по умолчанию отсюда (поле ввода и расчёт, если поле
не задано), интерфейс показывает статус и источник, а параметры-допущения считаются в смете и
сводном бюджете («итог содержит N допущений»). Ответ специалиста — правка одной записи файла,
без правки кода.

Формат хранения — JSON рядом с нормами (`data/norms/`), а не таблица справочника расценок:
правила правит человек, а справочник `data/references.sqlite` пересобирается из эталонов."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "pravila_parametrov.json"

STATUS_NORM = "norm"
STATUS_PRACTICE = "practice"
STATUS_ASSUMPTION = "assumption"
STATUS_INPUT = "input"
STATUS_LABELS = {
    STATUS_NORM: "норма",
    STATUS_PRACTICE: "практика компании",
    STATUS_ASSUMPTION: "допущение — ждёт ответа",
    STATUS_INPUT: "ввод оператора (зависит от объекта)",
}
SCOPE_ESTIMATE = "estimate"
SCOPE_OBJECT = "object"

# Обоснования оператора, если он изменил значение параметра с правилом: словарь
# {поле: текст} в вводе этапа (рядом со значениями полей; расчёт его не читает).
REASONS_KEY = "_param_reasons"


@dataclass(frozen=True)
class ParamRule:
    module: str
    field: str
    scope: str
    title: str
    depends_on: str
    default: Any
    etalon_value: Any
    etalon_where: str
    status: str
    source: str
    question: str
    default_text: str = ""  # как назвать значение по умолчанию человеку («по отметке объекта»)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def is_assumption(self) -> bool:
        return self.status == STATUS_ASSUMPTION


class ParamRulesError(ValueError):
    """Файл правил повреждён или не соответствует формату."""


def load_rules(path: str | Path | None = None) -> dict[tuple[str, str], ParamRule]:
    """Правила `{(раздел, поле): правило}`; ошибка формата — `ParamRulesError` с понятным
    текстом (а не тихое «правил нет»). Без `path` — `RULES_PATH` (тесты подменяют его)."""
    return _load_rules(str(path or RULES_PATH))


@lru_cache(maxsize=4)
def _load_rules(path: str) -> dict[tuple[str, str], ParamRule]:
    try:
        # utf-8-sig: Блокнот при сохранении добавляет метку BOM — это не ошибка файла
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ParamRulesError(f"не читается справочник правил {path}: {exc}") from exc
    items = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ParamRulesError(f"не читается справочник правил {path}: сверху ожидается объект с "
                              "ключом «rules» — списком правил")
    rules: dict[tuple[str, str], ParamRule] = {}
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ParamRulesError(f"правило №{number}: ожидается объект, а не {type(item).__name__}")
        try:
            etalon = item.get("etalon") or {}
            rule = ParamRule(
                module=item["module"], field=item["field"], scope=item["scope"],
                title=item["title"], depends_on=item.get("depends_on", ""),
                default=item.get("default"), etalon_value=etalon.get("value"),
                etalon_where=etalon.get("where", ""), status=item["status"],
                source=item.get("source", ""), question=item.get("question", ""),
                default_text=item.get("default_text", ""),
            )
        except KeyError as exc:
            raise ParamRulesError(f"правило №{number}: нет обязательного поля {exc}") from exc
        if rule.status not in STATUS_LABELS:
            raise ParamRulesError(f"правило №{number} ({rule.module}.{rule.field}): статус «{rule.status}»")
        if rule.scope not in (SCOPE_ESTIMATE, SCOPE_OBJECT):
            raise ParamRulesError(f"правило №{number} ({rule.module}.{rule.field}): область «{rule.scope}»")
        if (rule.module, rule.field) in rules:
            raise ParamRulesError(f"правило {rule.module}.{rule.field} записано дважды")
        problem = _default_problem(rule.default)
        if problem:
            raise ParamRulesError(f"правило №{number} ({rule.module}.{rule.field}): значение "
                                  f"по умолчанию {rule.default!r} — {problem}")
        rules[(rule.module, rule.field)] = rule
    return rules


# Числа в правилах — коэффициенты и доли; больше этого — опечатка (125 вместо 1,25 давала
# итог в миллиарды без единого сигнала; аудит 05.11.2026, О-8).
DEFAULT_MAX = 10.0


def _default_problem(value: Any) -> str:
    """Почему значение по умолчанию нельзя принять; пусто — можно."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return "не число"
        if not 0 <= value <= DEFAULT_MAX:
            return f"коэффициент вне пределов 0…{DEFAULT_MAX:g}, проверьте запись"
        return ""
    if isinstance(value, str):
        # Коды вариантов («5», «off») допустимы; дробь с запятой — ошибка записи.
        if "," in value and value.replace(",", "", 1).strip().isdigit():
            return "число записано текстом с запятой; пишите 1.25 без кавычек"
        return ""
    return f"недопустимый тип {type(value).__name__}"


def rule(module: str, field: str) -> ParamRule | None:
    """Правило параметра; файл правил повреждён — None (программа работает на прежних
    значениях модулей, ошибку показывает страница «Справочники» — `load_error`)."""
    try:
        return load_rules().get((module, field))
    except ParamRulesError:
        return None


def load_error() -> str:
    """Текст ошибки файла правил или пусто."""
    try:
        load_rules()
    except ParamRulesError as exc:
        return str(exc)
    return ""


def rules_for(module: str, scope: str = SCOPE_ESTIMATE) -> list[ParamRule]:
    try:
        rules = load_rules().values()
    except ParamRulesError:
        return []
    return [r for r in rules if r.module == module and r.scope == scope]


def default(module: str, field: str, fallback: Any = None) -> Any:
    """Значение по умолчанию параметра из справочника правил; правила нет — `fallback`
    (модули передают сюда прежнюю константу, чтобы без файла правил ничего не менялось)."""
    found = rule(module, field)
    if found is None:
        return fallback
    # Числовому параметру — только число: текст в правиле не должен дойти до расчёта.
    if (isinstance(fallback, (int, float)) and not isinstance(fallback, bool)
            and not isinstance(found.default, (int, float))):
        return fallback
    return found.default


def _blank(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def same_value(value: Any, default_value: Any) -> bool:
    """Значение ввода равно значению правила (пустое = по умолчанию; числа — с допуском)."""
    if _blank(value):
        return True  # поле не задано — расчёт берёт значение по умолчанию
    if _blank(default_value):
        # Числовое поле без значения программы форма показывает как 0 — это «не задано»
        # (например, районный к техотчёту ИГМИ: 0 — как к камеральным); ревью 03.11, №1.
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0
    try:
        return math.isclose(float(value), float(default_value), rel_tol=1e-9, abs_tol=1e-12)
    except (TypeError, ValueError):
        return str(value) == str(default_value)


@dataclass(frozen=True)
class ParamUse:
    """Как в смете использован параметр с правилом: `kind` — «допущение» (значение
    правила-допущения), «изменено» (оператор изменил, есть обоснование), «без
    обоснования» (изменил, обоснования нет)."""
    rule: ParamRule
    value: Any
    kind: str
    reason: str = ""


USE_ASSUMPTION = "assumption"
USE_CHANGED = "changed"
USE_NO_REASON = "no_reason"


def param_uses(module: str, inputs: dict | None, fields=None) -> list[ParamUse]:
    """Разбор параметров этапа сметы раздела с правилами (кроме статуса «ввод оператора»:
    там нет значения программы, которое можно «принять» или «изменить»). `fields` — имена
    полей этапа: правила других этапов не рассматриваются (на ведомости их полей нет)."""
    inputs = dict(inputs or {})
    reasons = inputs.get(REASONS_KEY) or {}
    allowed = None if fields is None else set(fields)
    uses = []
    for item in rules_for(module):
        if item.status == STATUS_INPUT or (allowed is not None and item.field not in allowed):
            continue
        value = inputs.get(item.field)
        if same_value(value, item.default):
            if item.is_assumption:
                uses.append(ParamUse(item, value, USE_ASSUMPTION))
            continue
        reason = str(reasons.get(item.field) or "").strip()
        uses.append(ParamUse(item, value, USE_CHANGED if reason else USE_NO_REASON, reason))
    return uses


def assumptions(module: str, inputs: dict | None, fields=None) -> list[ParamRule]:
    """Правила-допущения, по которым посчитан раздел (значение оператор не менял)."""
    return [u.rule for u in param_uses(module, inputs, fields) if u.kind == USE_ASSUMPTION]


def object_assumptions(data: Any, module_codes) -> list[ParamRule]:
    """Правила-допущения уровня объекта, действующие на разделы `module_codes`: пункт
    внутреннего транспорта по расстоянию (геология, экология) и коэффициент к организации
    и ликвидации (раздел, у которого задана её ставка). У ИГМИ начисления свои. Поля этапа
    сметы их не содержат, поэтому `assumptions` их не видит (аудит 05.11.2026, С-2)."""
    codes = set(module_codes)
    applies = {
        "distance_km": bool(codes & {"geology", "ecology"}),
        "org_liquidation_k": any(
            data.rate(code, "org_liquidation_rate") is not None
            for code in codes & {"geology", "geophysics", "geodesy", "ecology"}
        ),
    }
    return [r for r in rules_for("object", SCOPE_OBJECT)
            if r.is_assumption and applies.get(r.field, False)]


def default_label(item: ParamRule) -> str:
    """Значение по умолчанию словами: «по отметке объекта», «1» и т.п."""
    return item.default_text or format_value(item.default)


def format_value(value: Any) -> str:
    if _blank(value):
        return "не задано"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return f"{value:g}".replace(".", ",")
    return str(value)


def describe(item: ParamRule) -> str:
    """Одна строка под полем: статус, значение программы, эталон. Источник и где в эталоне — на
    странице «Справочники»; номер вопроса — для разработчика, оператору он ничего не говорит."""
    parts = [f"Правило: {item.status_label}"]
    if item.status != STATUS_INPUT:
        parts.append(f"по умолчанию {default_label(item)}")
    if item.etalon_value is not None:
        parts.append(f"в эталоне {format_value(item.etalon_value)}")
    return " · ".join(parts)
