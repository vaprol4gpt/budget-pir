"""Импорт заполненного шаблона «Ставки себестоимости» в локальный слой норм.

КАК ЗАПОЛНИТЬ И ИМПОРТИРОВАТЬ
1. Шаблон — `docs/templates/stavki_sebestoimosti.xlsx` (делает
   `tools/make_stavki_template.py`). Бухгалтерия вписывает в колонку
   «Значение 2026» фактические затраты компании (не цены для заказчика; без
   рентабельности и НДС) и в колонку «Источник» — документ-основание.
   Не знает — оставляет строку пустой (программа считает по допущению).
2. Сначала посмотреть, что изменится, ничего не записывая:
       .venv/Scripts/python.exe -m tools.import_stavki ФАЙЛ.xlsx --dry-run
3. Записать: `... -m tools.import_stavki ФАЙЛ.xlsx`. Если
   `data/local/norms_firm.json` уже есть — без `--force` программа его НЕ
   перезаписывает; с `--force` сначала кладёт рядом копию
   `norms_firm_до_импорта_<дата-время>.json`, затем СЛИВАЕТ: значения из
   шаблона заменяют прежние, пустые строки шаблона и всё, чего в шаблоне нет,
   остаётся как было. Другой путь вывода — `--out ФАЙЛ.json`.
4. Перезапустить программу: страница «4. Минимальный бюджет» берёт оклады и
   цены топлива как значения по умолчанию, остальные ставки — как нормы
   (в «Справочниках» у них «Откуда: локально (компании)»).

Куда пишется (с 03.11.2026, трек 3 «два дня»): общие ставки всех разделов
(оклады по должностям, начисления на ФОТ, содержание партии, проезд, топливо,
резерв) — в раздел "cost_common" локального слоя, особые ставки — в раздел
своего вида работ ("geology_cost", "ecology_cost", …; `Article.target`).
Раньше общие ставки записывались в "geology_cost"; при импорте такой же
общей ставки старая запись геологии УБИРАЕТСЯ (иначе она перекрыла бы
ответ бухгалтерии для геологии — `core/cost.py::load_cost_norms`), то же —
оклады геологии «водитель» и «камеральщик» при импорте общих «водитель» и
«инженер камеральной группы». Каждое такое удаление — строка «−» в отчёте;
прежний файл остаётся в копии.

Проверки. ОШИБКА (ничего не записывается): значение не число или меньше 0;
единица в файле не та, что ждёт программа; ключ, которого программа не знает;
значение без источника. Почему пустой источник — ошибка, а не
предупреждение: локальный слой без источника не читается
(`core/norms.py` бросает NormsFormatError) — записать такой файл значит
сломать страницу минимального бюджета; к тому же без основания ставка
ничем не лучше допущения. ПРЕДУПРЕЖДЕНИЕ (запись идёт): значение в 10 и
более раз больше или меньше текущего допущения программы
(`data/norms/*.json` своего раздела); буровой инструмент + запчасти на 1 п.м.
больше всей фактической себестоимости 1 п.м. по архиву
(`reference_data/archive_unit_costs.json`); пустая строка «Кто заполнил»;
статья шаблона отсутствует в файле (шаблон старый).

Проценты (начисления на ФОТ, резерв) в шаблоне — в процентах, в нормах —
долей: 30,2 % → 0,302.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

import core.norms
from core.cost import INPUT_ALIASES, LEGACY_NORMS, load_cost_norms
from tools.make_stavki_template import (
    ALL_ARTICLES,
    COMMON,
    HEADERS,
    LABEL_DATE,
    LABEL_WHO,
    NORMS_NAME,
    UNUSED_ARTICLES,
    UNUSED_TITLE,
    Article,
)

ROOT = Path(__file__).resolve().parent.parent
OUTLIER_FACTOR = 10  # во сколько раз отличие от допущения считается выбросом
BACKUP_PREFIX = "norms_firm_до_импорта_"


@dataclass
class Taken:
    article: Article
    value: float  # в единицах нормы (проценты уже переведены в долю)
    raw: float  # как в шаблоне
    source: str
    comment: str


@dataclass
class ImportReport:
    taken: list[Taken] = field(default_factory=list)
    empty: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unused_filled: list[str] = field(default_factory=list)
    filled_by: str = ""
    filled_on: str = ""
    out_path: Path | None = None
    backup_path: Path | None = None
    written: bool = False
    changes: list[str] = field(default_factory=list)

    def text(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"ОШИБКИ ({len(self.errors)}) — файл норм НЕ записан:")
            lines += [f"  × {e}" for e in self.errors]
        lines.append(f"Ставок взято: {len(self.taken)} из {len(ALL_ARTICLES)}.")
        for item in self.taken:
            lines.append(f"  ✓ {item.article.label}: {_num(item.raw)} {item.article.unit} "
                         f"(источник: {item.source})")
        if self.empty:
            lines.append(f"Пустые (остаются как были — допущение или прежний локальный слой): "
                         f"{len(self.empty)}:")
            lines += [f"  · {a.label}" for a in self.empty]
        if self.unused_filled:
            lines.append("Заполнены статьи, которые программа пока не использует (не перенесены):")
            lines += [f"  · {u}" for u in self.unused_filled]
        if self.warnings:
            lines.append(f"Предупреждения ({len(self.warnings)}) — проверьте:")
            lines += [f"  ! {w}" for w in self.warnings]
        if self.changes:
            lines.append("Изменения в файле норм:")
            lines += [f"  {c}" for c in self.changes]
        if self.backup_path:
            lines.append(f"Копия прежнего файла: {self.backup_path}")
        if self.written:
            lines.append(f"Записано: {self.out_path}")
        elif not self.errors:
            lines.append("Ничего не записано (просмотр).")
        return "\n".join(lines)


def _num(value: float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")


def _parse_number(value) -> float | None | str:
    """Число из ячейки; None — пусто; строка — текст ошибки."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return "не число"
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return f"не число («{value}»)"


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%d.%m.%Y")
    return str(value).strip()


def read_template(path: Path, report: ImportReport) -> None:
    """Читает шаблон в `report` (взятые, пустые, ошибки, предупреждения)."""
    ws = load_workbook(path, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = next((i for i, r in enumerate(rows) if list(r[:len(HEADERS)]) == HEADERS), None)
    if header is None:
        report.errors.append("в файле нет таблицы шаблона (строка заголовков «Статья, Ключ, Единица, "
                             "Значение 2026…» не найдена) — это не тот файл?")
        return
    for r in rows[:header]:
        if r and r[0] == LABEL_WHO:
            report.filled_by = _cell_text(r[1])
        elif r and r[0] == LABEL_DATE:
            report.filled_on = _cell_text(r[1])
    by_key = {a.key: a for a in ALL_ARTICLES}
    seen = set()
    unused_labels = {label for label, _u, _r in UNUSED_ARTICLES}
    in_unused = False
    for number, r in enumerate(rows[header + 1:], start=header + 2):
        r = list(r) + [None] * 6
        if r[0] == UNUSED_TITLE:
            in_unused = True
            continue
        if in_unused:
            if r[0] in unused_labels and any(_cell_text(x) for x in r[3:6]):
                report.unused_filled.append(f"{r[0]}: {_cell_text(r[3])} {r[2] or ''} "
                                            f"(источник: {_cell_text(r[4]) or 'не указан'})")
            continue
        key = _cell_text(r[1])
        if not key:
            continue  # заголовок группы или пояснение
        article = by_key.get(key)
        if article is None:
            report.errors.append(f"строка {number}: ключ «{key}» программе неизвестен — шаблон "
                                 "не от этой версии программы или ключ изменён")
            continue
        seen.add(key)
        label = f"строка {number} «{article.label}»"
        unit = _cell_text(r[2])
        if unit != article.unit:
            report.errors.append(f"{label}: единица «{unit}», а программа ждёт «{article.unit}»")
            continue
        parsed = _parse_number(r[3])
        source, comment = _cell_text(r[4]), _cell_text(r[5])
        if isinstance(parsed, str):
            report.errors.append(f"{label}: значение {parsed}")
            continue
        if parsed is None:
            report.empty.append(article)
            continue
        if parsed < 0:
            report.errors.append(f"{label}: отрицательное значение {_num(parsed)}")
            continue
        if not source:
            report.errors.append(f"{label}: значение есть, а источник (документ-основание) пуст")
            continue
        report.taken.append(Taken(article, parsed * article.scale, parsed, source, comment))
    for article in ALL_ARTICLES:
        if article.key not in seen:
            report.warnings.append(f"статьи «{article.label}» нет в файле — шаблон старый? "
                                   "Сделайте новый: tools/make_stavki_template.py")
    if not report.filled_by:
        report.warnings.append("не заполнено «Кто заполнил» — источник ставок будет неполным")


def check_outliers(report: ImportReport, archive_path: Path | None = None) -> None:
    """Предупреждения о выбросах: против seed-допущений и архива себестоимости."""
    seeds: dict[str, dict] = {}
    for item in report.taken:
        if item.article.target not in seeds:
            seeds[item.article.target] = load_cost_norms(item.article.target, use_local=False)
        base = seeds[item.article.target].get(item.article.key)
        if item.article.section != "norms" or base is None or not base.value:
            continue
        ratio = item.value / float(base.value)
        if ratio >= OUTLIER_FACTOR or ratio <= 1 / OUTLIER_FACTOR:
            times = "в ∞" if ratio == 0 else f"в {_num(round(max(ratio, 1 / ratio), 1))}"
            report.warnings.append(
                f"«{item.article.label}»: {_num(item.raw)} {item.article.unit} — {times} "
                f"раз(а) {'больше' if ratio > 1 else 'меньше'} допущения программы "
                f"({_num(float(base.value) / item.article.scale)}). Опечатка или другая единица?"
            )
    taken = {t.article.key: t.value for t in report.taken}
    seed = load_cost_norms(NORMS_NAME, use_local=False)
    if archive_path is None:  # настоящий архив, если он есть, иначе демонстрационный
        from modules.geology.cost import archive_path as default_archive

        archive_path = default_archive()
    if {"drilling_tools_per_pm", "spare_parts_per_pm"} & set(taken) and archive_path.exists():
        per_pm = sum(taken.get(k, float(seed[k].value or 0))
                     for k in ("drilling_tools_per_pm", "spare_parts_per_pm"))
        facts = [o["fact_cost_per_m"] for o in json.loads(archive_path.read_text(encoding="utf-8"))
                 ["objects"] if o.get("fact_cost_per_m")]
        if facts and per_pm > min(facts):
            report.warnings.append(
                f"буровой инструмент + запчасти = {_num(per_pm)} руб./п.м. — больше всей фактической "
                f"себестоимости 1 п.м. у объекта архива ({_num(min(facts))} руб./п.м., "
                f"файл {archive_path.name}). Проверьте."
            )


def _entry(item: Taken, report: ImportReport, stamp: str) -> dict:
    entry = {"value": item.value, "source": item.source}
    if item.article.section == "norms":
        entry["assumption"] = False
    note = item.comment or ""
    entry["note"] = note
    entry["imported"] = stamp
    entry["filled_by"] = report.filled_by
    entry["filled_on"] = report.filled_on
    # Отметка «ставка от бухгалтерии» и её дата — по ним расчёт минимума показывает
    # статус калибровки (core/norms.py, KIND_ACCOUNTING; modules/geology/cost.py::rates_status).
    entry["kind"] = core.norms.KIND_ACCOUNTING
    entry["date"] = _iso_date(report.filled_on) or stamp[:10]
    return entry


def _iso_date(text: str) -> str:
    """«03.11.2026» / «2026-11-03» → «2026-11-03»; не дата — пусто."""
    text = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def merge(existing: dict, report: ImportReport, stamp: str) -> dict:
    """Слияние: значения шаблона поверх прежнего файла, остальное сохраняется."""
    result = json.loads(json.dumps(existing)) if existing else {}
    result.setdefault("_формат", core.norms.NORMS_FORMAT_VERSION)
    result.setdefault("_описание", "Локальный слой норм компании (не в git). Сделан tools/import_stavki.py "
                                   "из шаблона docs/templates/stavki_sebestoimosti.xlsx.")
    result.setdefault(NORMS_NAME, {})
    # общая ставка вместо прежней записи геологии: (раздел записи, ключ в геологии)
    legacy_keys = {v: k for k, v in INPUT_ALIASES.get(LEGACY_NORMS, {}).items()}
    for item in report.taken:
        section = result.setdefault(item.article.target, {})
        group = section.setdefault(item.article.section, {})
        old = group.get(item.article.key, {}).get("value")
        group[item.article.key] = _entry(item, report, stamp)
        where = "" if item.article.target == NORMS_NAME else f"{item.article.target}."
        if old is None:
            report.changes.append(f"+ {where}{item.article.key}: {_num(item.value)}")
        elif float(old) != item.value:
            report.changes.append(f"~ {where}{item.article.key}: {_num(float(old))} → {_num(item.value)}")
        if item.article.target == COMMON:
            legacy_group = result.get(LEGACY_NORMS, {}).get(item.article.section, {})
            legacy_key = legacy_keys.get(item.article.key, item.article.key)
            if legacy_key in legacy_group:
                legacy_group.pop(legacy_key)
                report.changes.append(
                    f"− {LEGACY_NORMS}.{legacy_key}: прежняя запись геологии убрана — теперь общая "
                    f"ставка {COMMON}.{item.article.key}")
    return result


def run_import(xlsx: Path, out: Path | None = None, force: bool = False, dry_run: bool = False,
               now: dt.datetime | None = None) -> ImportReport:
    now = now or dt.datetime.now()
    out = out or (core.norms.LOCAL_DIR / core.norms.LOCAL_FILE)
    report = ImportReport(out_path=out)
    read_template(Path(xlsx), report)
    if report.errors:
        return report
    check_outliers(report)
    existing = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report.errors.append(f"существующий {out} не читается ({exc}) — исправьте или "
                                 "переименуйте его; слить с ним нельзя")
            return report
        if not force and not dry_run:
            report.errors.append(f"{out} уже есть — не перезаписываю. Посмотрите изменения "
                                 "(--dry-run) и запустите с --force (будет копия прежнего файла)")
            merge(existing, report, now.isoformat(timespec="seconds"))
            return report
    merged = merge(existing, report, now.isoformat(timespec="seconds"))
    if dry_run:
        return report
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        backup = out.with_name(f"{BACKUP_PREFIX}{now:%Y-%m-%d_%H%M%S}.json")
        shutil.copy2(out, backup)
        report.backup_path = backup
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    report.written = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Импорт шаблона «Ставки себестоимости» в локальный слой норм data/local/")
    parser.add_argument("xlsx", type=Path, help="заполненный шаблон .xlsx")
    parser.add_argument("--out", type=Path, default=None,
                        help="файл локального слоя (по умолчанию data/local/norms_firm.json)")
    parser.add_argument("--force", action="store_true",
                        help="записать поверх существующего файла (с копией и слиянием)")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что изменится")
    args = parser.parse_args(argv)
    report = run_import(args.xlsx, args.out, force=args.force, dry_run=args.dry_run)
    print(report.text())
    return 1 if report.errors else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
