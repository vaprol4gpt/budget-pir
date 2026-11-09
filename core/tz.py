"""Параметры технического задания (ТЗ) и черновик ведомости объёмов из них
(шаг 1 → 2 процесса компании, 03.11.2026; исследование — docs/generator_vedomosti.md).

ТЗ заказчика объёмов работ не задаёт (docs/generator_vedomosti.md §2): в нём только
«каркасные» параметры объекта — вид, протяжённость или площадь, масштаб и сечение
съёмки, число искусственных сооружений (ИССО — мосты и трубы), наличие
многолетнемёрзлых грунтов (ММГ, вечная мерзлота), сейсмичность, состав разделов
изысканий. Ключевые количественные решения (шаг и глубина выработок, ширина полосы
съёмки) принимает исполнитель по своду правил (СП) и практике — оператор вводит их
отдельно, с подписью «решение исполнителя».

Поэтому генератор только **предлагает** черновик ввода этапа «Ведомость объёмов»
каждого выбранного раздела: модуль раздела по параметрам ТЗ заполняет поля своего
ввода (необязательный метод модуля `propose_volumes(tz) -> Proposal`; модуль без
него работает как раньше — так же обратно совместимо расширялся контракт `Field`).
Формулы самого модуля не дублируются: привязку, камеральную обработку, термометрию,
монолиты и т.п. модуль, как и прежде, считает сам из своего ввода на этапе
ведомости (классы связей А/Б/В). Ничего не подтверждается автоматически.

У каждого предложенного значения — происхождение (`KIND_*`): из ТЗ, решение
исполнителя, правило (жёсткая связь), оценка (проверьте); поля, которые из ТЗ не
выводятся, остаются пустыми — «ввод». Повторное предложение не затирает ручной ввод
молча (`merge_proposal`: по умолчанию заполняются только пустые поля).

Режим «ключевые объёмы» (03.11.2026): инженер задаёт по разделу
3–5 главных объёмов (`TzParams.key_volumes`, поля — необязательный метод модуля
`key_volume_fields()`), а модуль достраивает остальные поля ввода
(`propose_from_key_volumes(tz, keys)`) по жёстким связям — проверены на обоих
эталонных объектах и имеют физический смысл («анализ = проба», «камеральная =
полевая») — и по оценкам архива (связь только на одном объекте или совпадение без
физического смысла) с пометкой «оценка по архиву — проверьте». Сверка —
docs/proverka_vedomosti.md, раздел 5.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any

# --- происхождение значения --------------------------------------------------

KIND_TZ = "tz"
KIND_EXECUTOR = "executor"
KIND_RULE = "rule"
KIND_ESTIMATE = "estimate"
KIND_INPUT = "input"
KIND_MANUAL = "manual"
KIND_KEY = "key"
KIND_ARCHIVE = "archive"
KIND_LABELS = {
    KIND_TZ: "из ТЗ",
    KIND_EXECUTOR: "решение исполнителя (стр. 1)",
    KIND_RULE: "правило (жёсткая связь)",
    KIND_ESTIMATE: "оценка — проверьте",
    KIND_INPUT: "ввод (из ТЗ не выводится)",
    KIND_MANUAL: "вручную (изменено после предложения)",
    KIND_KEY: "ключевой объём (стр. 1)",
    KIND_ARCHIVE: "оценка по архиву — проверьте",
}

# Как предлагать ведомость: только по ТЗ и решениям исполнителя или по ключевым
# объёмам разделов (поверх предложения по ТЗ).
PROPOSE_TZ = "tz"
PROPOSE_KEY = "key"
PROPOSE_MODE_LABELS = {
    PROPOSE_TZ: "По параметрам ТЗ и решениям исполнителя",
    PROPOSE_KEY: "По ключевым объёмам разделов (инженер задаёт главные объёмы, программа "
                 "достраивает остальные)",
}

# --- параметры ТЗ ------------------------------------------------------------

OBJECT_LINEAR = "linear"
OBJECT_AREA = "area"
OBJECT_KINDS = {
    "": "не указан",
    OBJECT_LINEAR: "Линейный (автодорога, трасса)",
    OBJECT_AREA: "Площадной (карьер, площадка)",
}
# Разделы изысканий (коды модулей, порядок — как в меню разделов).
SECTION_LABELS = {
    "geology": "Инженерно-геологические (геология)",
    "geophysics": "Инженерно-геофизические (геофизика)",
    "geodesy": "Инженерно-геодезические (геодезия)",
    "ecology": "Инженерно-экологические (экология)",
    "hydromet": "Инженерно-гидрометеорологические (гидрометеорология)",
}
# Масштабы топографической съёмки (тот же ряд, что у модуля геодезии).
SURVEY_SCALES = ["1:500", "1:1000", "1:2000", "1:5000", "1:10000"]

# Подписи полей (страница «1. Объект и ТЗ», лист «Сведения» выгрузки).
TZ_LABELS = {
    "object_kind": "Вид объекта",
    "length_km": "Протяжённость трассы, км",
    "area_ha": "Площадь участка, га",
    "stage": "Этап (стадия) изысканий",
    "survey_scale": "Масштаб топографической съёмки",
    "survey_section_m": "Сечение рельефа, м",
    "isso_count": "Число искусственных сооружений (ИССО: мосты и трубы)",
    "permafrost": "Многолетнемёрзлые грунты (ММГ)",
    "seismicity": "Сейсмичность, баллов",
    "sections": "Состав разделов изысканий",
    "holes_count": "Число скважин",
    "hole_spacing_m": "Шаг выработок вдоль трассы, м",
    "hole_depth_m": "Средняя глубина скважины, м",
    "strip_width_m": "Ширина полосы съёмки (вся, по обе стороны от оси), м",
}
# Решения исполнителя — по СП и практике, в ТЗ их нет (подпись в интерфейсе).
EXECUTOR_FIELDS = ("holes_count", "hole_spacing_m", "hole_depth_m", "strip_width_m")
NUMBER_FIELDS = ("length_km", "area_ha", "survey_section_m", "seismicity",
                 "hole_spacing_m", "hole_depth_m", "strip_width_m")
INTEGER_FIELDS = ("isso_count", "holes_count")


def _number(value: Any) -> float | None:
    """Число из сохранённого значения или ввода; пусто, не число, 0 и меньше —
    None (все параметры ТЗ — положительные величины)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _key_item(value: Any) -> float | str | None:
    """Значение ключевого объёма: число (> 0) или код варианта (непустая строка-не-число)."""
    number = _number(value)
    if number is not None:
        return number
    if isinstance(value, str) and value.strip():
        try:
            float(value.strip().replace(" ", "").replace(",", "."))
        except ValueError:
            return value.strip()
    return None


@dataclass(frozen=True)
class TzParams:
    """Параметры ТЗ + решения исполнителя. Все необязательные: None / "" — не
    задано (тогда зависящие от них позиции остаются вводом оператора)."""

    object_kind: str = ""
    length_km: float | None = None
    area_ha: float | None = None
    stage: str = ""
    survey_scale: str = ""
    survey_section_m: float | None = None
    isso_count: int | None = None
    permafrost: bool | None = None     # None — в ТЗ не указано
    seismicity: float | None = None
    sections: tuple[str, ...] = ()
    # решения исполнителя (по СП и практике — в ТЗ их нет)
    holes_count: int | None = None
    hole_spacing_m: float | None = None
    hole_depth_m: float | None = None
    strip_width_m: float | None = None
    # ключевые объёмы по разделам: {код модуля: {поле: число или код варианта}}.
    # Словарь в замороженном классе: в хеш не входит (hash=False) — TzParams можно
    # класть в множества/ключи, сравнение (==) по-прежнему учитывает поле.
    key_volumes: dict = field(default_factory=dict, hash=False)

    @classmethod
    def from_dict(cls, data: dict | None) -> "TzParams":
        """Из сохранённого словаря / ввода формы. Неизвестные ключи и битые
        значения пропускаются (старые и более новые данные открываются)."""
        data = data if isinstance(data, dict) else {}
        values: dict[str, Any] = {}
        kind = data.get("object_kind")
        if kind in OBJECT_KINDS:
            values["object_kind"] = kind
        for name in ("stage",):
            if isinstance(data.get(name), str):
                values[name] = data[name].strip()
        if data.get("survey_scale") in SURVEY_SCALES:
            values["survey_scale"] = data["survey_scale"]
        for name in NUMBER_FIELDS:
            values[name] = _number(data.get(name))
        for name in INTEGER_FIELDS:
            number = _number(data.get(name))
            values[name] = int(round(number)) if number is not None else None
        if isinstance(data.get("permafrost"), bool):
            values["permafrost"] = data["permafrost"]
        sections = data.get("sections")
        if isinstance(sections, (list, tuple)):
            values["sections"] = tuple(code for code in SECTION_LABELS if code in sections)
        keys = data.get("key_volumes")
        if isinstance(keys, dict):
            # только известные разделы; положительные числа или непустые коды вариантов
            # (выпадающие списки); пусто / 0 — не задано
            values["key_volumes"] = {
                code: cleaned for code, items in keys.items()
                if code in SECTION_LABELS and isinstance(items, dict)
                if (cleaned := {str(name): parsed for name, value in items.items()
                                if (parsed := _key_item(value)) is not None})
            }
        return cls(**values)

    def to_dict(self) -> dict:
        """Только заданные значения (пустые параметры — пустой словарь)."""
        result = {}
        for item in dataclasses.fields(self):
            value = getattr(self, item.name)
            if value in (None, "", (), {}):
                continue
            if isinstance(value, dict):
                value = {code: dict(items) for code, items in value.items()}
            result[item.name] = list(value) if isinstance(value, tuple) else value
        return result

    def is_empty(self) -> bool:
        return not self.to_dict()

    def describe(self) -> dict[str, str]:
        """Заданные параметры — подпись: значение (лист «Сведения», сводки)."""
        rows = {}
        for name, value in self.to_dict().items():
            if name == "object_kind":
                value = OBJECT_KINDS[value]
            elif name == "permafrost":
                value = "есть" if value else "нет"
            elif name == "sections":
                value = ", ".join(SECTION_LABELS[c].split(" (")[1].rstrip(")") for c in value)
            elif name == "key_volumes":
                rows["Ключевые объёмы"] = ", ".join(
                    f"{SECTION_LABELS[c].split(' (')[1].rstrip(')')} ({len(v)})" for c, v in value.items())
                continue
            elif isinstance(value, float):
                value = f"{value:g}".replace(".", ",")
            rows[TZ_LABELS[name]] = str(value)
        return rows

    def warnings(self) -> list[str]:
        """Противоречия и пробелы, мешающие предложить ведомость."""
        result = []
        if self.object_kind == OBJECT_LINEAR and self.length_km is None:
            result.append("Линейный объект: не задана протяжённость трассы — объёмы по длине "
                          "(съёмка, рекогносцировка, изыскания трассы) не будут предложены.")
        if self.object_kind == OBJECT_AREA and self.area_ha is None:
            result.append("Площадной объект: не задана площадь участка — площадь съёмки не "
                          "будет предложена.")
        if self.holes_count is None and self.hole_spacing_m is not None and self.length_km is None:
            result.append("Шаг выработок задан, но нет протяжённости трассы — число скважин "
                          "не считается.")
        return result

    # --- величины, общие для нескольких разделов --------------------------------
    # Считаются здесь один раз, модули берут готовое (с происхождением).

    def holes(self) -> "Derived | None":
        """Число скважин: решение исполнителя напрямую или протяжённость / шаг."""
        if self.holes_count is not None:
            return Derived(self.holes_count, KIND_EXECUTOR, "число скважин — решение исполнителя")
        if self.object_kind == OBJECT_LINEAR and self.length_km and self.hole_spacing_m:
            # округление до 9 знаков перед ceil: 8,05 км / 50 м = 161, а не 162 из-за
            # двоичной записи дробей (ревью 03.11, №2)
            count = math.ceil(round(self.length_km * 1000 / self.hole_spacing_m, 9))
            return Derived(
                count, KIND_EXECUTOR,
                f"из решения исполнителя (шаг выработок): = протяжённость {self.length_km:g} км / "
                f"шаг {self.hole_spacing_m:g} м "
                "(с округлением вверх); дополнительные выработки на ИССО не добавлены — "
                "правила нет",
            )
        return None

    def drilling_total(self) -> "Derived | None":
        """Метраж бурения всего = число скважин × средняя глубина."""
        holes = self.holes()
        if holes is None or not self.hole_depth_m:
            return None
        return Derived(
            round(holes.value * self.hole_depth_m, 2), KIND_RULE,
            f"= число скважин {holes.value} × глубина {self.hole_depth_m:g} м",
        )

    def survey_area(self) -> "Derived | None":
        """Площадь топографической съёмки, га: линейный объект —
        протяжённость × ширина полосы / 10 000; площадной — площадь участка из ТЗ."""
        if self.object_kind == OBJECT_LINEAR and self.length_km and self.strip_width_m:
            area = round(self.length_km * 1000 * self.strip_width_m / 10_000, 4)
            return Derived(
                area, KIND_RULE,
                f"= {self.length_km:g} км × {self.strip_width_m:g} м / 10 000",
            )
        if self.object_kind == OBJECT_AREA and self.area_ha:
            return Derived(self.area_ha, KIND_TZ, "площадь участка из ТЗ")
        return None


@dataclass(frozen=True)
class Derived:
    value: Any
    kind: str
    note: str


# --- предложение ввода этапа ведомости ---------------------------------------


@dataclass
class Proposal:
    """Черновик ввода этапа «Ведомость объёмов» одного раздела.

    `inputs` — {поле ввода: значение} только для предложенных полей;
    `sources` — {поле: (вид происхождения KIND_*, пояснение)}, в т.ч. для полей,
    оставленных вводом оператора (KIND_INPUT) с причиной; `notes` — что
    проверить после предложения (не значения полей)."""

    inputs: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, tuple[str, str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def set(self, name: str, value: Any, kind: str, note: str) -> None:
        self.inputs[name] = value
        self.sources[name] = (kind, note)

    def set_derived(self, name: str, derived: Derived | None, why_missing: str,
                    convert=lambda v: v) -> None:
        """Значение из общей величины ТЗ или пометка «ввод» с причиной."""
        if derived is None:
            self.leave(name, why_missing)
        else:
            self.set(name, convert(derived.value), derived.kind, derived.note)

    def leave(self, name: str, why: str) -> None:
        """Поле остаётся вводом оператора (в черновик не попадает)."""
        self.sources[name] = (KIND_INPUT, why)


def propose_stage_input(module: Any, tz: TzParams, mode: str = PROPOSE_TZ) -> Proposal | None:
    """Предложение модуля по параметрам ТЗ или None, если у модуля нет
    необязательного метода `propose_volumes`. В режиме `PROPOSE_KEY` — по
    ключевым объёмам раздела (`propose_from_key_volumes`), если модуль его
    умеет и объёмы раздела заданы; иначе — как по ТЗ."""
    keys = tz.key_volumes.get(getattr(module, "code", ""), {})
    if mode == PROPOSE_KEY and keys:  # ключевых объёмов раздела нет — как по ТЗ
        method = getattr(module, "propose_from_key_volumes", None)
        if callable(method):
            return method(tz, keys)
    method = getattr(module, "propose_volumes", None)
    return method(tz) if callable(method) else None


def key_volume_fields(module: Any) -> list:
    """Поля ключевых объёмов раздела (`Field`) или [], если модуль режима не умеет."""
    method = getattr(module, "key_volume_fields", None)
    return list(method()) if callable(method) else []


def key_value(keys: dict | None, name: str, integer: bool = False) -> float | int | None:
    """Ключевой объём из ввода: положительное число или None (не задан)."""
    number = _number((keys or {}).get(name))
    if number is None:
        return None
    return int(round(number)) if integer else number


def key_text(keys: dict | None, name: str) -> str | None:
    """Ключевой объём-вариант (код из выпадающего списка) или None."""
    value = (keys or {}).get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def key_volume_options(module: Any, name: str) -> list[tuple[str, str]]:
    """Варианты выпадающего списка ключевого объёма (`Field.dynamic_options`):
    необязательный метод модуля `key_volume_options(name)`; нет — []."""
    method = getattr(module, "key_volume_options", None)
    return list(method(name)) if callable(method) else []


KEY_NOTE = "ключевой объём — задан инженером на странице 1"


def link_note(text: str) -> str:
    """Пояснение жёсткой связи (проверена на обоих эталонных объектах)."""
    return f"жёсткая связь: {text}"


def archive_note(text: str) -> str:
    """Пояснение оценки по архиву (связь на одном объекте или без физического смысла)."""
    return f"оценка по архиву: {text} — проверьте"


# --- слияние с уже введённым ---------------------------------------------------

MODE_FILL_EMPTY = "fill_empty"
MODE_REPLACE = "replace"
MODE_LABELS = {
    MODE_FILL_EMPTY: "Заполнить только пустые поля (введённое вручную не трогать)",
    MODE_REPLACE: "Заменить введённое предложением (что заменено — будет показано)",
}


def is_empty_value(value: Any) -> bool:
    """Пусто ли поле ввода: None, 0, "" — пусто; группа строк пуста, если ни в
    одной строке нет ненулевого числа (строка из одних выбранных по умолчанию
    вариантов — тоже пустая: так форма рисует новую строку)."""
    if isinstance(value, (list, tuple)):
        return all(_row_empty(row) for row in value)
    if isinstance(value, bool):
        return not value
    return value in (None, 0, 0.0, "")


def _row_empty(row: Any) -> bool:
    if not isinstance(row, dict):
        return is_empty_value(row)
    numbers = [v for v in row.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return not any(numbers)


def same_value(current: Any, proposed: Any) -> bool:
    """Совпадает ли текущее значение поля с предложенным. У строк группы
    сравниваются только под-поля, заданные в предложении (форма дописывает
    остальные значениями по умолчанию)."""
    if isinstance(proposed, (list, tuple)):
        if not isinstance(current, (list, tuple)) or len(current) != len(proposed):
            return False
        return all(
            all(v is None or same_value((c or {}).get(k), v) for k, v in p.items())
            for c, p in zip(current, proposed)
        )
    if isinstance(proposed, (int, float)) and isinstance(current, (int, float)):
        return math.isclose(float(current), float(proposed), rel_tol=1e-9, abs_tol=1e-9)
    return current == proposed


@dataclass(frozen=True)
class MergeResult:
    inputs: dict
    filled: list[str]     # пустые поля, заполненные предложением
    kept: list[str]       # введённые поля, оставленные (предложение другое)
    replaced: list[str]   # введённые поля, заменённые предложением
    # поля, где стояло прошлое предложение программы (оператор его не менял), —
    # заменены новым предложением в любом режиме
    updated: list[str] = field(default_factory=list)


def merge_proposal(current: dict | None, proposed: dict, mode: str = MODE_FILL_EMPTY,
                   previous: dict | None = None) -> MergeResult:
    """Ввод этапа после «Предложить ведомость». Поля, которых нет в
    предложении, не трогаются. `MODE_FILL_EMPTY` (по умолчанию) — заполняются
    только пустые поля, введённое остаётся (список `kept`); `MODE_REPLACE` —
    предложенные поля заменяются, заменённое непустое — в `replaced`.

    `previous` — ввод прошлого предложения программы в этой же сессии. Значение,
    которое с тех пор не менялось (совпадает с прошлым предложением), — не ввод
    оператора, а черновик программы: его новое предложение заменяет в любом
    режиме (список `updated`). Так «предложить по ТЗ, затем по ключевым объёмам»
    ставит ключевые числа, а ручной ввод по-прежнему защищён (ревью трека 1, №5)."""
    result = dict(current or {})
    previous = previous or {}
    filled, kept, replaced, updated = [], [], [], []
    for name, value in proposed.items():
        old = result.get(name)
        if is_empty_value(old):
            result[name] = _copy(value)
            filled.append(name)
        elif same_value(old, value):
            continue
        elif name in previous and same_value(old, previous[name]):
            result[name] = _copy(value)
            updated.append(name)
        elif mode == MODE_REPLACE:
            result[name] = _copy(value)
            replaced.append(name)
        else:
            kept.append(name)
    return MergeResult(result, filled, kept, replaced, updated)


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [dict(row) if isinstance(row, dict) else row for row in value]
    return value


# --- откуда значения: таблица для страницы ведомости -----------------------------


def format_value(value: Any) -> str:
    """Значение поля ввода для таблицы «откуда значения»."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return f"{value:g}".replace(".", ",")
    if isinstance(value, (list, tuple)):
        rows = []
        for row in value:
            if isinstance(row, dict):
                parts = [format_value(v) for v in row.values() if v not in (None, "")]
                rows.append(" / ".join(parts))
            else:
                rows.append(format_value(row))
        return "; ".join(rows) or "—"
    return str(value)


def provenance_rows(fields: list, record: dict, current: dict | None) -> list[dict]:
    """Строки таблицы «откуда значения» по полям этапа: предложенные поля и
    обязательные поля, оставленные вводом. `record` — {"inputs": …, "sources": …}
    последнего предложения; `current` — текущий ввод формы. Значение,
    изменённое после предложения, помечается «вручную»."""
    current = current or {}
    proposed = record.get("inputs", {})
    sources = record.get("sources", {})
    rows = []
    for item in fields:
        if item.name not in sources and not item.required:
            continue
        kind, note = sources.get(item.name, (KIND_INPUT, "из ТЗ не выводится"))
        now = current.get(item.name)
        if item.name in proposed and not is_empty_value(now) and not same_value(now, proposed[item.name]):
            kind, note = KIND_MANUAL, f"предложено: {format_value(proposed[item.name])}; {note}"
        elif kind == KIND_INPUT and not is_empty_value(now):
            kind = KIND_MANUAL
        rows.append({
            "Поле ввода": item.label,
            "Сейчас": format_value(now),
            "Предложено": format_value(proposed.get(item.name)),
            "Источник": KIND_LABELS[kind],
            "Пояснение": note,
        })
    return rows
