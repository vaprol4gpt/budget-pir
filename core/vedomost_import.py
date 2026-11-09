"""Ведомость объёмов работ из файла Excel и обратно (03.11.2026, трек «импорт ведомости»).

Видение: отдел загружает свою ведомость объёмов файлом Excel, программа её
проверяет и ставит в этап 1 «Ведомость объёмов» так же, как при ручном вводе.

**Что импортируется — значения полей ввода этапа 1**, а не готовые строки
ведомости. Ведомость по-прежнему строит модуль (`build_volume_sheet`): связи
(камеральная обработка = полевой объём, привязка = число скважин, программа,
отчёт…), проверки и правка строк работают как при ручном вводе, а файл не может
обойти расчёт. Готовые строки ведомости импортировать нельзя: половина из них
выводится модулем из ввода, и их ручной ввод разошёлся бы со связями.

Перечень «видов работ» шаблона выводится из полей этапа 1 модуля
(`stages()[0].fields`), без правки модулей и без списков, набранных руками:

- числовое поле — один вид работ; наименование — из подписи поля
  («Крепление скважин, п.м.» → «Крепление скважин»); единица — строки ведомости
  модуля с тем же id, что у поля (её оператор увидит после загрузки: «Число
  скважин» → «скв.»), иначе из подписи («п.м.»);
- повторяющаяся группа (`Field.type="group"`: бурение по категориям грунта,
  съёмка по масштабам, пробы, анализы) — по виду работ на каждый вариант выбора
  («Бурение по категориям грунта: категория грунта IV»); прочие поля строки
  группы (сечение рельефа, условие съёмки у геодезии) — отдельные колонки листа;
- поле-выбор этапа 1 (только у геофизики: тип волн МПВ, «да/нет» камеральной
  обработки ВЭЗ и техотчёта) — отметка «да/нет»: объём 1 — выбран, 0 или нет
  строки — значение по умолчанию.

Чтение (`read_vedomost`) — без Streamlit: ошибки по строкам (неизвестный вид
работ, не та единица, объём не число / отрицательный / дробный там, где штуки /
пустой при указанном виде, дубли, файл другого раздела) и предупреждения →
значения ввода этапа 1. Файл с ошибками в ввод не ставится. Запись шаблона и
выгрузка текущего ввода — `core/vedomost_template.py`.
"""

from __future__ import annotations

import difflib
import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from core.module_base import Field

# Метка раздела в шаблоне (строка над таблицей): «… · код раздела: geology · шаблон v1».
TEMPLATE_VERSION = 1
SECTION_MARK = "код раздела"
_SECTION_RE = re.compile(SECTION_MARK + r"\s*:\s*([a-z_]+)", re.IGNORECASE)
# Код вида работ перед названием «ИГИ-10 · Крепление скважин» (формат универсального
# шаблона ведомости отделов, docs/templates/README.md) — отбрасывается при поиске.
_CODE_PREFIX_RE = re.compile(r"^\s*[A-Za-zА-Яа-яЁё]{2,6}-\d+\s*·\s*")

SHEET_VEDOMOST = "Ведомость"
SHEET_CATALOG = "Справочник"
SHEET_HELP = "Как заполнить"

COL_WORK = "Вид работ"
COL_UNIT = "Единица"
COL_QTY = "Объём"
COL_NOTE = "Примечание"
# Скрытая защищённая колонка: постоянный ключ вида работ (поле модуля [= вариант]).
# По нему файл читается и после того, как подпись поля в программе изменилась.
COL_KEY = "Ключ (служебный)"
# Виды работ, которые отделы привыкли считать «производными» (программа, камеральная
# обработка, отчёт): если такие есть в списке раздела, их нужно указать в файле самим.
DERIVED_HINTS = ("программ", "камеральн", "отчёт")

KIND_NUMBER = "number"   # числовое поле этапа 1
KIND_GROUP = "group"     # вариант выбора повторяющейся группы
KIND_FLAG = "flag"       # поле-выбор: вариант (или «да») отмечается объёмом 1

FLAG_UNIT = "да/нет"
FALLBACK_UNIT = "шт."
# Длина «единицы» после последней запятой подписи: дальше — уже не единица, а часть названия.
_UNIT_MAX_LEN = 25
# Короткий вариант выбора («IV», «1:500») пишется вместе с подписью поля выбора.
_SHORT_OPTION_LEN = 8
_PER_UNIT_RE = re.compile(r"^(.*?)\s*\(за\s+([^()]+)\)\s*$")
# Метка ячейки «формула без вычисленного значения» при чтении.
FORMULA_NO_VALUE = object()


@dataclass(frozen=True)
class WorkType:
    """Вид работ шаблона — одна позиция выпадающего списка."""

    name: str
    unit: str
    field: str            # имя поля этапа 1
    kind: str             # KIND_NUMBER / KIND_GROUP / KIND_FLAG
    integer: bool = False
    option: Any = None    # значение выбора (группа — главного выбора строки, флаг — варианта)
    form_label: str = ""  # подпись поля в форме программы (для листа «Справочник»)

    @property
    def key(self) -> str:
        """Постоянный ключ: имя поля модуля, у варианта — «поле=значение»."""
        return self.field if self.kind == KIND_NUMBER else f"{self.field}={self.option}"

    @property
    def looks_derived(self) -> bool:
        return any(hint in self.name.casefold() for hint in DERIVED_HINTS)

    @property
    def hint(self) -> str:
        if self.kind == KIND_FLAG:
            return "1 — да (выбрано), 0 — нет"
        return "целое число" if self.integer else "число"


@dataclass(frozen=True)
class ParamColumn:
    """Дополнительная колонка листа — поле строки группы, кроме главного выбора
    и объёма (у геодезии: «Сечение рельефа, м», «Условие съёмки (коэффициент)»).
    `subfields` — {имя группы: имя поля строки}, `options` — {имя группы:
    [(значение, подпись)]} (только у выбора)."""

    header: str
    type: str  # "number" / "choice"
    subfields: dict[str, str]
    options: dict[str, list[tuple[Any, str]]] = field(default_factory=dict)

    def all_labels(self) -> list[str]:
        """Подписи вариантов всех групп без повторов, по порядку."""
        seen: list[str] = []
        for opts in self.options.values():
            for _value, label in opts:
                if label not in seen:
                    seen.append(label)
        return seen


@dataclass
class GroupSpec:
    """Как читается группа: главный выбор, объём, прочие поля (колонки)."""

    field: Field
    choice: Field
    qty: Field
    params: list[Field]
    options: list[tuple[Any, str]]


@dataclass
class Catalog:
    """Перечень видов работ раздела, выведенный из полей этапа 1 модуля."""

    module_code: str
    fields: list[Field]
    work_types: list[WorkType]
    params: list[ParamColumn]
    groups: dict[str, GroupSpec]
    choice_options: dict[str, list[tuple[Any, str]]]
    unsupported: list[str]

    def find(self, name: Any) -> WorkType | None:
        key = norm(_CODE_PREFIX_RE.sub("", "" if name is None else str(name)))
        for work in self.work_types:
            if norm(work.name) == key:
                return work
        return None

    def by_key(self, key: Any) -> WorkType | None:
        if _is_blank(key):
            return None
        for work in self.work_types:
            if work.key == str(key).strip():
                return work
        return None

    def derived_in_list(self) -> list[WorkType]:
        """Виды работ раздела вроде программы, камеральной обработки, отчёта, которые
        есть в списке: их нужно указать в файле, сами они не добавятся."""
        return [w for w in self.work_types if w.looks_derived]

    def suggest(self, name: Any) -> str | None:
        names = {norm(w.name): w.name for w in self.work_types}
        close = difflib.get_close_matches(norm(name), list(names), n=1, cutoff=0.6)
        return names[close[0]] if close else None

    def param(self, header: str) -> ParamColumn | None:
        for column in self.params:
            if norm(column.header) == norm(header):
                return column
        return None

    def columns(self) -> list[str]:
        """Заголовки колонок листа «Ведомость» по порядку."""
        return [COL_WORK, COL_UNIT, COL_QTY, *[p.header for p in self.params], COL_NOTE, COL_KEY]


def norm(value: Any) -> str:
    """Текст для сравнения: без регистра, лишних пробелов, «ё» = «е»."""
    text = "" if value is None else str(value)
    return " ".join(text.replace(" ", " ").split()).casefold().replace("ё", "е")


def _peel_trailing_parens(text: str) -> tuple[str, str]:
    """('основа', '(хвост)') — последняя скобка в конце строки (с вложенными)."""
    text = text.rstrip()
    if not text.endswith(")"):
        return text, ""
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                return text[:i].rstrip(), text[i:]
    return text, ""


def _cut_note(text: str) -> str:
    """Отрезать пояснение после « — » (вне скобок) и хвостовые скобки про эталон."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text.startswith(" — ", i):
            text = text[:i]
            break
    while True:
        base, tail = _peel_trailing_parens(text)
        if tail and "эталон" in tail.casefold():
            text = base
            continue
        return text.strip()


def split_label(label: str) -> tuple[str, str | None]:
    """Подпись поля → (наименование вида работ, единица | None).

    «Крепление скважин, п.м.» → («Крепление скважин», «п.м.»);
    «Объём сейсморазведки МПВ, физ.наблюдений (на один тип волн)» →
    («Объём сейсморазведки МПВ (на один тип волн)», «физ.наблюдений»);
    «Число скважин» → («Число скважин», None)."""
    main = _cut_note(label)
    base, tail = _peel_trailing_parens(main)
    match = re.match(r"^(.*\S),\s*([^,()]{1,%d})$" % _UNIT_MAX_LEN, base)
    if match:
        name = match.group(1) + (f" {tail}" if tail else "")
        return name, match.group(2).strip()
    return main, None


def _line_units(module: Any, fields: list[Field]) -> dict[str, str]:
    """Единицы строк ведомости модуля, id которых совпадает с именем поля
    (у геодезии «1 пункт» → «пункт»). Ввод — 1 в каждом числовом поле."""
    if not hasattr(module, "build_volume_sheet"):
        return {}
    data = {f.name: 1 for f in fields if f.type == "number"}
    try:
        sheet = module.build_volume_sheet(data)
    except Exception:  # noqa: BLE001 — единица из подписи всё равно есть у большинства полей
        return {}
    units = {}
    for line_id, line in sheet.items():
        unit = str(getattr(line, "unit", "") or "").strip()
        units[line_id] = re.sub(r"^1\s+", "", unit)
    return units


def _option_text(choice: Field, label: str) -> tuple[str, str | None]:
    """Подпись варианта → (текст для названия вида работ, единица «за …» | None)."""
    text = _cut_note(label)
    unit = None
    match = _PER_UNIT_RE.match(text)
    if match:
        text, unit = match.group(1).strip(), match.group(2).strip()
    if len(text) <= _SHORT_OPTION_LEN:
        text = f"{choice.label.casefold()} {text}"
    return text, unit


def _first_stage_fields(module: Any) -> list[Field]:
    from core.stages import as_staged

    return list(as_staged(module).stages[0].fields)


def build_catalog(module: Any, refs: Any, module_code: str | None = None) -> Catalog:
    """Перечень видов работ раздела по полям этапа 1 модуля. `refs` нужен
    вариантам выбора из справочника (категории грунта геологии)."""
    code = module_code or getattr(module, "code", "")
    fields = _first_stage_fields(module)
    line_units = _line_units(module, fields)
    works: list[WorkType] = []
    params: dict[str, ParamColumn] = {}
    groups: dict[str, GroupSpec] = {}
    choices: dict[str, list[tuple[Any, str]]] = {}
    unsupported: list[str] = []

    for f in fields:
        if f.type == "group":
            subs = f.group_fields or []
            choice = next((s for s in subs if s.dynamic_options), None)
            numbers = [s for s in subs if s.type == "number"]
            qty = next((s for s in numbers if s.required), numbers[0] if numbers else None)
            if choice is None or qty is None:
                unsupported.append(f.label)
                continue
            options = list(module.field_options(choice.name, refs))
            rest = [s for s in subs if s is not choice and s is not qty]
            groups[f.name] = GroupSpec(f, choice, qty, rest, options)
            group_name, _ = split_label(f.label)
            _, qty_unit = split_label(qty.label)
            for value, label in options:
                text, per_unit = _option_text(choice, label)
                works.append(WorkType(
                    f"{group_name}: {text}", per_unit or qty_unit or FALLBACK_UNIT, f.name,
                    KIND_GROUP, qty.integer, value, f"{f.label} → {choice.label}: {label}",
                ))
            for sub in rest:
                column = params.setdefault(sub.label, ParamColumn(
                    sub.label, "choice" if sub.dynamic_options else "number", {}, {}))
                column.subfields[f.name] = sub.name
                if sub.dynamic_options:
                    column.options[f.name] = list(module.field_options(sub.name, refs))
        elif f.dynamic_options:
            options = list(module.field_options(f.name, refs))
            if not options:
                unsupported.append(f.label)
                continue
            choices[f.name] = options
            name = _cut_note(f.label)
            default_label = options[0][1]
            if len(options) == 2 and norm(default_label).startswith("нет"):
                works.append(WorkType(name, FLAG_UNIT, f.name, KIND_FLAG, True, options[1][0],
                                      f.label))
            else:
                for value, label in options[1:]:
                    works.append(WorkType(f"{name} — {_cut_note(label)}", FLAG_UNIT, f.name,
                                          KIND_FLAG, True, value, f"{f.label}: {label}"))
        elif f.type == "number":
            # Единица — как в строке ведомости модуля с тем же id (её оператор увидит
            # после загрузки), иначе из подписи поля.
            name, unit = split_label(f.label)
            unit = line_units.get(f.name) or unit or FALLBACK_UNIT
            works.append(WorkType(name, unit, f.name, KIND_NUMBER, f.integer, None, f.label))
        else:
            unsupported.append(f.label)
    return Catalog(code, fields, works, list(params.values()), groups, choices, unsupported)


# --- значения ввода ------------------------------------------------------------


def empty_inputs(catalog: Catalog) -> dict:
    """Ввод этапа 1 «ничего не делаем»: числа 0, группы пусты, выборы — первый
    вариант (как форма по умолчанию)."""
    data: dict[str, Any] = {}
    for f in catalog.fields:
        if f.type == "group":
            data[f.name] = [] if f.name in catalog.groups else None
        elif f.name in catalog.choice_options:
            data[f.name] = catalog.choice_options[f.name][0][0]
        elif f.type == "number":
            data[f.name] = 0 if f.integer else 0.0
    return data


def _is_zero(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, (int, float)) and value == 0)


@dataclass
class ExportRow:
    """Строка листа «Ведомость» при выгрузке ввода."""

    work: WorkType
    qty: float
    params: dict[str, Any] = field(default_factory=dict)  # заголовок колонки → значение/подпись


def export_rows(catalog: Catalog, inputs: dict | None) -> tuple[list[ExportRow], list[str]]:
    """Ввод этапа 1 → строки листа (без нулевых объёмов и значений по умолчанию)
    + сообщения о том, что в шаблон не попало (вариант выбора не из списка)."""
    inputs = inputs or {}
    rows: list[ExportRow] = []
    skipped: list[str] = []
    by_field_option = {(w.field, w.option): w for w in catalog.work_types}
    for f in catalog.fields:
        value = inputs.get(f.name)
        if f.name in catalog.groups:
            spec = catalog.groups[f.name]
            for number, row in enumerate(value or [], start=1):
                qty = row.get(spec.qty.name)
                if _is_zero(qty):
                    continue
                work = by_field_option.get((f.name, row.get(spec.choice.name)))
                if work is None:
                    skipped.append(f"{f.label}, строка {number}: «{spec.choice.label}» = "
                                   f"{row.get(spec.choice.name)!r} — нет в списке вариантов")
                    continue
                params = {}
                for sub in spec.params:
                    column = catalog.param(sub.label)
                    sub_value = row.get(sub.name)
                    if sub_value is None:
                        continue
                    if column.type == "choice":
                        labels = dict(column.options.get(f.name, []))
                        if sub_value not in labels:
                            skipped.append(f"{f.label}, строка {number}: «{sub.label}» = "
                                           f"{sub_value!r} — нет в списке вариантов")
                            continue
                        params[column.header] = labels[sub_value]
                    else:
                        params[column.header] = sub_value
                rows.append(ExportRow(work, qty, params))
        elif f.name in catalog.choice_options:
            default = catalog.choice_options[f.name][0][0]
            if value is None or value == default:
                continue
            work = by_field_option.get((f.name, value))
            if work is None:
                skipped.append(f"{f.label}: значение {value!r} — нет в списке вариантов")
                continue
            rows.append(ExportRow(work, 1))
        elif f.type == "number" and not _is_zero(value):
            work = by_field_option.get((f.name, None))
            if work is not None:
                rows.append(ExportRow(work, value))
    return rows, skipped


def has_input(catalog: Catalog, inputs: dict | None) -> bool:
    """В вводе есть хоть один ненулевой объём или выбор не по умолчанию."""
    rows, skipped = export_rows(catalog, inputs)
    return bool(rows or skipped)


def normalized_inputs(catalog: Catalog, inputs: dict | None) -> dict:
    """Ввод в сравнимом виде: числа — float, пропуски — 0 / по умолчанию, строки
    групп без нулевого объёма, поля строки без значения не пишутся (для
    кругового теста «выгрузил → загрузил → тот же ввод»)."""
    inputs = inputs or {}
    result: dict[str, Any] = {}
    for f in catalog.fields:
        value = inputs.get(f.name)
        if f.name in catalog.groups:
            spec = catalog.groups[f.name]
            rows = []
            for row in value or []:
                if _is_zero(row.get(spec.qty.name)):
                    continue
                item = {spec.choice.name: row.get(spec.choice.name),
                        spec.qty.name: float(row.get(spec.qty.name))}
                for sub in spec.params:
                    sub_value = row.get(sub.name)
                    if sub.dynamic_options:
                        options = catalog.param(sub.label).options.get(f.name) or [(None, "")]
                        sub_value = sub_value if sub_value is not None else options[0][0]
                    elif sub_value is not None:
                        sub_value = float(sub_value)
                    if sub_value is not None:
                        item[sub.name] = sub_value
                rows.append(item)
            result[f.name] = rows
        elif f.name in catalog.choice_options:
            default = catalog.choice_options[f.name][0][0]
            result[f.name] = default if value is None else value
        elif f.type == "number":
            result[f.name] = 0.0 if _is_zero(value) else float(value)
    return result


# --- чтение файла ----------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """Ошибка или предупреждение; `row` — номер строки листа Excel (None — файл целиком)."""

    row: int | None
    text: str

    def __str__(self) -> str:
        return f"Строка {self.row}: {self.text}" if self.row else self.text


@dataclass
class PreviewRow:
    row: int
    work: str
    unit: str
    qty: Any
    params: str
    note: str
    target: str  # куда встанет: поле формы


@dataclass
class ImportResult:
    module_code: str
    inputs: dict
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    preview: list[PreviewRow] = field(default_factory=list)
    file_section: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_number(value: Any) -> float | None:
    """Число из ячейки: число или текст «1 234,5». None — не число (в том числе
    «nan», «inf» — их float() принимает, а объёмом они быть не могут)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return None
    return number if math.isfinite(number) else None


# Объём больше этого — скорее опечатка (ни на одном объекте близко нет): замечание, не ошибка.
VOLUME_PLAUSIBLE_MAX = 1e7

# Допуск «целого» числа: =0,1*30 в Excel даёт 3,0000000000000004.
_INT_TOLERANCE = 1e-9


def _as_integer(number: float) -> int | None:
    nearest = round(number)
    return int(nearest) if abs(number - nearest) < _INT_TOLERANCE else None


def norm_unit(value: Any) -> str:
    """Единица для сравнения: «п. м.» = «п.м» = «п.м.», «дм2» = «дм²», «м3» = «м³»."""
    text = norm(value)
    text = re.sub(r"\s*\.\s*", ".", text).rstrip(".")
    return re.sub(r"м([23])(?![0-9])", lambda m: "м" + {"2": "²", "3": "³"}[m.group(1)], text)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _load_workbooks(source: Any):
    """(книга со значениями, книга с формулами): формула без вычисленного значения
    (файл собран программой и не сохранялся в Excel) в первой — пусто, во второй —
    текст «=…», по нему её и узнаём."""
    from openpyxl import load_workbook

    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    elif isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = source.read()
    return (load_workbook(io.BytesIO(data), data_only=True),
            load_workbook(io.BytesIO(data), data_only=False))


def _find_sheet(wb) -> Any:
    if SHEET_VEDOMOST in wb.sheetnames:
        return wb[SHEET_VEDOMOST]
    for ws in wb.worksheets:
        if _find_header(ws) is not None:
            return ws
    return wb.worksheets[0]


def _find_header(ws) -> tuple[int, dict[str, int]] | None:
    """(номер строки заголовка, {норм. заголовок: номер колонки}) или None."""
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 30)):
        headers = {norm(c.value): c.column for c in row if not _is_blank(c.value)}
        # «Вид работ», а также «Вид работы (выбор)» и т.п. — по началу заголовка
        if any(_header_matches(name, COL_WORK) for name in headers):
            return row[0].row, headers
    return None


def _file_section(ws, header_row: int) -> str | None:
    for row in ws.iter_rows(min_row=1, max_row=header_row, values_only=True):
        for value in row:
            if isinstance(value, str):
                match = _SECTION_RE.search(value)
                if match:
                    return match.group(1)
    return None


def _header_matches(header: str, title: str) -> bool:
    """Заголовок колонки подходит к названию: совпадает или начинается с него целым
    словом («Объём, осн. ед.», «Объём работ (по ТЗ)» — да; «Объёмный вес» — нет).
    «Вид работ» подходит и к «Вид работы (выбор)»."""
    key = norm(title)
    if header == key:
        return True
    if not header.startswith(key):
        return False
    rest = header[len(key):]
    if key == norm(COL_WORK) and rest.startswith("ы"):
        rest = rest[1:]
    return not rest or not rest[0].isalnum()


def _col(headers: dict[str, int], title: str,
         originals: dict[int, str] | None = None) -> tuple[int | None, str | None]:
    """(номер колонки, ошибка). Несколько подходящих колонок — ошибка с их перечнем
    (`originals` — {колонка: заголовок как в файле}, для сообщения)."""
    originals = originals or {}
    key = norm(title)
    if key in headers:
        return headers[key], None
    found = [(name, col) for name, col in headers.items() if _header_matches(name, title)]
    if len(found) > 1:
        names = ", ".join(f"«{originals.get(c, name)}»" for name, c in found)
        return None, (f"к колонке «{title}» подходят несколько: {names} — оставьте одну "
                      f"или назовите нужную точно «{title}».")
    return (found[0][1] if found else None), None


def read_vedomost(
    source: Any, module: Any, refs: Any, module_code: str | None = None,
    section_labels: dict[str, str] | None = None, sum_duplicates: bool = False,
) -> ImportResult:
    """Разобрать файл ведомости (путь, байты или файловый объект) для раздела
    `module`. `section_labels` — {код раздела: подпись} для понятного сообщения
    «файл другого раздела». `sum_duplicates=True` — одинаковые строки (ведомость
    по сооружениям) складываются с предупреждением, иначе дубль — ошибка."""
    catalog = build_catalog(module, refs, module_code)
    code = catalog.module_code
    labels = section_labels or {}
    result = ImportResult(code, empty_inputs(catalog))
    errors, warnings = result.errors, result.warnings

    try:
        wb, wb_formulas = _load_workbooks(source)
    except Exception as exc:  # noqa: BLE001 — любой не-xlsx: сообщение, а не падение
        errors.append(Issue(None, f"Не удалось открыть файл как книгу Excel (.xlsx): {exc}"))
        return result
    ws = _find_sheet(wb)
    ws_formulas = wb_formulas[ws.title]
    found = _find_header(ws)
    if found is None:
        errors.append(Issue(None, f"В файле не найдена таблица с колонкой «{COL_WORK}» — "
                                  "возьмите шаблон ведомости из программы."))
        return result
    header_row, headers = found
    section = _file_section(ws, header_row)
    result.file_section = section
    if section is not None and section != code:
        errors.append(Issue(None, f"Файл — ведомость раздела «{labels.get(section, section)}», а "
                                  f"выбран раздел «{labels.get(code, code)}». Выберите нужный раздел "
                                  "в боковой панели или загрузите файл своего раздела."))
        return result
    if section is None:
        warnings.append(Issue(None, "В файле нет метки раздела шаблона программы — проверены "
                                    "только колонки и виды работ."))
    originals = {c.column: _cell_text(c.value) for c in ws[header_row] if not _is_blank(c.value)}
    found_cols = {title: _col(headers, title, originals)
                  for title in (COL_WORK, COL_UNIT, COL_QTY, COL_NOTE, COL_KEY,
                                *[p.header for p in catalog.params])}
    for _title, (_c, problem) in found_cols.items():
        if problem:
            errors.append(Issue(header_row, problem))
    if errors:
        return result
    cols = {title: c for title, (c, _p) in found_cols.items()}
    col_work, col_unit, col_qty = cols[COL_WORK], cols[COL_UNIT], cols[COL_QTY]
    col_note, col_key = cols[COL_NOTE], cols[COL_KEY]
    if col_qty is None:
        errors.append(Issue(header_row, f"нет колонки «{COL_QTY}»."))
        return result
    param_cols = {p.header: cols[p.header] for p in catalog.params}

    seen: dict[tuple, tuple[int, PreviewRow, Any]] = {}
    flag_rows: dict[str, tuple[int, WorkType]] = {}
    group_rows: dict[str, list[tuple[int, dict]]] = {name: [] for name in catalog.groups}
    number_values: dict[str, tuple[int, float]] = {}

    def cell(row, col):
        """Значение ячейки; формула без вычисленного значения — FORMULA_NO_VALUE."""
        if not col:
            return None
        value = ws.cell(row=row, column=col).value
        if value is None:
            formula = ws_formulas.cell(row=row, column=col).value
            if isinstance(formula, str) and formula.startswith("="):
                return FORMULA_NO_VALUE
        return value

    def formula_error(r, title):
        errors.append(Issue(r, f"в колонке «{title}» формула без вычисленного значения — откройте "
                               "файл в Excel и сохраните его (Excel посчитает формулы), затем "
                               "загрузите снова."))

    for r in range(header_row + 1, ws.max_row + 1):
        raw_work, raw_qty = cell(r, col_work), cell(r, col_qty)
        raw_unit, raw_note = cell(r, col_unit), cell(r, col_note)
        raw_key = cell(r, col_key)
        raw_params = {h: cell(r, c) for h, c in param_cols.items()}
        if all(_is_blank(v) or v is FORMULA_NO_VALUE
               for v in (raw_work, raw_qty, raw_note, *raw_params.values())):
            continue
        bad_formula = [title for title, v in ((COL_WORK, raw_work), (COL_QTY, raw_qty),
                                              *raw_params.items()) if v is FORMULA_NO_VALUE]
        if bad_formula:
            for title in bad_formula:
                formula_error(r, title)
            continue
        if raw_unit is FORMULA_NO_VALUE:
            raw_unit = None  # единица шаблона — формула; без значения не проверяется
        if raw_key is FORMULA_NO_VALUE:
            raw_key = None
        if raw_note is FORMULA_NO_VALUE:
            raw_note = None
        if _is_blank(raw_work):
            if not _is_blank(raw_qty) or any(not _is_blank(v) for v in raw_params.values()):
                errors.append(Issue(r, "указан объём, но не выбран вид работ."))
            continue
        # Сначала — по постоянному ключу (служебная колонка шаблона), потом по названию.
        by_key, by_name = catalog.by_key(raw_key), catalog.find(raw_work)
        if (by_key is not None and by_name is not None and by_key.key != by_name.key
                and norm(by_key.name) != norm(by_name.name)):
            # Выгруженный из программы файл: ключ записан значением, и после смены вида
            # работ в строке он остаётся от прежнего. Оператор видит название — ему и
            # верим (аудит 05.11.2026, О-3: объём молча уходил в прежний вид работ).
            warnings.append(Issue(r, f"в строке выбран вид работ «{by_name.name}», а служебный ключ "
                                     f"строки остался от «{by_key.name}» (вид работ сменили после "
                                     f"выгрузки) — объём отнесён к «{by_name.name}»."))
            work = by_name
        else:
            work = by_key or by_name
        if work is None:
            hint = catalog.suggest(raw_work)
            errors.append(Issue(r, f"неизвестный вид работ «{_cell_text(raw_work)}»"
                                   + (f" — может быть, «{hint}»?" if hint else
                                      " — выберите вид работ из списка шаблона.")))
            continue
        if not _is_blank(raw_unit) and norm_unit(raw_unit) != norm_unit(work.unit):
            errors.append(Issue(r, f"единица «{_cell_text(raw_unit)}» не та: для «{work.name}» — "
                                   f"«{work.unit}». Пересчитайте объём в «{work.unit}»."))
            continue
        if _is_blank(raw_qty):
            errors.append(Issue(r, f"не указан объём для «{work.name}»."))
            continue
        qty = _parse_number(raw_qty)
        if qty is None:
            errors.append(Issue(r, f"объём «{_cell_text(raw_qty)}» — не число."))
            continue
        if qty < 0:
            errors.append(Issue(r, f"объём {qty:g} отрицательный."))
            continue
        if work.kind == KIND_FLAG and qty not in (0, 1):
            errors.append(Issue(r, f"для «{work.name}» объём — 1 (да) или 0 (нет), указано {qty:g}."))
            continue
        if work.integer and _as_integer(qty) is None:
            errors.append(Issue(r, f"для «{work.name}» нужно целое число ({work.unit}), указано {qty:g}."))
            continue
        value: Any = _as_integer(qty) if work.integer else qty
        if qty > VOLUME_PLAUSIBLE_MAX:
            warnings.append(Issue(r, f"объём {qty:g} {work.unit} для «{work.name}» — необычно большой, "
                                     "проверьте запись."))

        params: dict[str, Any] = {}
        key_params: dict[str, Any] = {}  # для поиска дублей: вариант по умолчанию = не указан
        param_texts = []
        bad_param = False
        for header, raw in raw_params.items():
            if _is_blank(raw):
                continue
            column = catalog.param(header)
            if work.kind != KIND_GROUP or work.field not in column.subfields:
                warnings.append(Issue(r, f"колонка «{header}» не относится к «{work.name}» — "
                                         "значение не использовано."))
                continue
            if column.type == "choice":
                options = column.options.get(work.field, [])
                match = next((v for v, label in options
                              if norm(label) == norm(raw) or norm(v) == norm(raw)), None)
                if match is None:
                    allowed = "; ".join(label for _v, label in options)
                    errors.append(Issue(r, f"«{header}»: «{_cell_text(raw)}» — нет в списке для "
                                           f"«{work.name}» (можно: {allowed})."))
                    bad_param = True
                    continue
                params[column.subfields[work.field]] = match
                if match != options[0][0]:
                    key_params[column.subfields[work.field]] = match
                param_texts.append(f"{header}: {dict(options)[match]}")
            else:
                number = _parse_number(raw)
                if number is None or number < 0:
                    errors.append(Issue(r, f"«{header}»: «{_cell_text(raw)}» — нужно число не меньше 0."))
                    bad_param = True
                    continue
                params[column.subfields[work.field]] = number
                key_params[column.subfields[work.field]] = number
                param_texts.append(f"{header}: {number:g}")
        if bad_param:
            continue

        preview = PreviewRow(r, work.name, work.unit, value, "; ".join(param_texts),
                             _cell_text(raw_note), work.form_label)
        key = (work.name, tuple(sorted(key_params.items())))
        if key in seen:
            first_row, first_preview, _ = seen[key]
            if sum_duplicates and work.kind != KIND_FLAG:
                first_preview.qty = first_preview.qty + value
                warnings.append(Issue(r, f"«{work.name}» уже есть в строке {first_row} — объёмы "
                                         f"сложены ({first_preview.qty:g} {work.unit})."))
                continue
            errors.append(Issue(r, f"«{work.name}» встречается второй раз (впервые — в строке "
                                   f"{first_row}). Оставьте одну строку или сложите объёмы"
                                   + ("" if work.kind == KIND_FLAG else
                                      " (или отметьте «Сложить одинаковые строки»)") + "."))
            continue
        seen[key] = (r, preview, params)
        result.preview.append(preview)

        if work.kind == KIND_FLAG:
            if qty == 1:
                if work.field in flag_rows:
                    other_row, other = flag_rows[work.field]
                    errors.append(Issue(r, f"выбрано и «{other.name}» (строка {other_row}), и "
                                           f"«{work.name}» — оставьте один вариант."))
                    continue
                flag_rows[work.field] = (r, work)
        elif work.kind == KIND_GROUP:
            group_rows[work.field].append((r, {"_preview": preview, "params": params, "work": work}))
        else:
            number_values[work.field] = (r, preview)

    if not result.preview and not errors:
        errors.append(Issue(None, "В файле нет ни одной строки с видом работ и объёмом."))
    if errors:
        return result

    # --- значения ввода ---
    inputs = result.inputs
    for field_name, (_r, preview) in number_values.items():
        inputs[field_name] = preview.qty
    for field_name, (_r, work) in flag_rows.items():
        inputs[field_name] = work.option
    for field_name, rows in group_rows.items():
        spec = catalog.groups[field_name]
        built = []
        for _r, item in rows:
            row = {spec.choice.name: item["work"].option, spec.qty.name: item["_preview"].qty}
            for sub in spec.params:
                if sub.name in item["params"]:
                    row[sub.name] = item["params"][sub.name]
                elif sub.dynamic_options:
                    # как форма: без выбора — первый вариант списка
                    options = catalog.param(sub.label).options.get(field_name) or []
                    if options:
                        row[sub.name] = options[0][0]
            built.append(row)
        inputs[field_name] = built

    for f in catalog.fields:
        value = inputs.get(f.name)
        if f.required and f.type == "group" and not value:
            warnings.append(Issue(None, f"обязательный вид работ «{f.label}» в файле не указан — "
                                        "заполните его в форме перед подтверждением."))
        elif f.required and f.type == "number" and f.positive and not value:
            warnings.append(Issue(None, f"обязательный вид работ «{f.label}» в файле не указан "
                                        "(или 0) — заполните его в форме перед подтверждением."))
    _warn_missing_derived(catalog, inputs, warnings)
    return result


def _warn_missing_derived(catalog: Catalog, inputs: dict, warnings: list[Issue]) -> None:
    """Программа, камеральная обработка, отчёт из списка раздела не указаны, а другие
    работы есть: отдел мог решить, что программа добавит их сама, — тогда работа
    молча пропала бы из ведомости. Предупреждение называет каждую."""
    by_field = {f.name: f for f in catalog.fields}
    derived = catalog.derived_in_list()
    derived_fields = {w.field for w in derived}
    has_other = any(not _is_default(catalog, by_field[name], value)
                    for name, value in inputs.items()
                    if name in by_field and name not in derived_fields)
    if not has_other:
        return
    reported = set()
    for work in derived:
        if work.field in reported or not _is_default(catalog, by_field[work.field],
                                                     inputs.get(work.field)):
            continue
        reported.add(work.field)
        warnings.append(Issue(None, f"в файле нет «{work.name}» — эта работа есть в списке "
                                    "раздела и сама не добавится: в ведомости её не будет. Если "
                                    "она нужна, добавьте строку в файл или включите её в форме."))


def _is_default(catalog: Catalog, f: Field, value: Any) -> bool:
    """Значение поля — «ничего не делаем»: 0, пустая группа, выбор по умолчанию."""
    if f.name in catalog.groups:
        return not value
    if f.name in catalog.choice_options:
        return value is None or value == catalog.choice_options[f.name][0][0]
    return _is_zero(value)


def iter_issue_rows(issues: Iterable[Issue]) -> list[dict]:
    """Ошибки/предупреждения таблицей для интерфейса."""
    return [{"Строка Excel": str(issue.row) if issue.row else "—", "Что не так": issue.text}
            for issue in issues]
