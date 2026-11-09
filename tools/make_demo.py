"""Демо-база сохранённых смет: два объекта с подтверждёнными сметами разделов.

Для показа программы (научному руководителю, на защите): открыл программу —
в базе уже лежат заполненные и подтверждённые бюджеты двух объектов, вводить
ничего не надо. На странице «Версии» — кнопка «Открыть все разделы расчёта».

Объекты — согласованные эталоны `reference_data/` (очищенные копии, без
заказчика): «Автодорога» (шифр условный «Т-01») и «Карьер» («Т-02»). Ввод —
тот же, что в эталонных тестах (там он уже повторяет эталонные листы так, как
его вводит оператор): объёмы ведомости и правки её строк, параметры сметы,
правки строк сметы, свои строки по прайсам. Функции ввода берутся из тестов
(`tests/test_*_etalon*.py`), чтобы демо и тесты не разошлись.

Что делает скрипт:
1. пересоздаёт базу (путь — аргумент, по умолчанию `work/demo/data/smety.sqlite`);
2. для каждого раздела объекта проходит оба этапа (ведомость → смета) и
   сохраняет их версиями одной цепочки (`SmetaStore.save_version`) с датой
   расчёта 30.09.2026 и данными объекта;
3. открывает каждую версию заново (`load_version_chain`), проверяет, что оба
   этапа подтверждены и программа на тех же данных получает ту же смету
   (иначе интерфейс показал бы «был / стал»), считает итог с индексом и НДС и
   сверяет со сводной эталона в допусках эталонных тестов. Отклонение сверх
   допуска — код выхода 1.

Не сохраняется (честно, без выдумок):
- минимальный бюджет (себестоимость): ставок себестоимости (оклады) в
  репозитории нет — расчёт минимума был бы на выдуманных числах;
- у Карьера нет геофизики — в эталоне нет листа ИГФИ, раздел не создаётся;
- три листа ИГИ Карьера сведены в одну смету геологии с суммой объёмов (в
  бюджете одна смета на раздел — так сделал бы оператор);
- коэффициенты снижения цены сохраняются в данных объекта, но открытие версии
  их на странице «5. Сводный бюджет» не подставляет (решение по цене объекта —
  ввод на странице 5, `app/ui.py::PRICE_DECISION_FIELDS`); программа при
  открытии сообщает, какое снижение было в версии.

Запуск из корня проекта:
    .venv/Scripts/python.exe -X utf8 -m tools.make_demo [путь к базе]
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE = "2026-09-30"
DEFAULT_DB = ROOT / "work" / "demo" / "data" / "smety.sqlite"
ROAD_XLSX = ROOT / "reference_data" / "Автодорога_эталон.xlsx"
KARIER_XLSX = ROOT / "reference_data" / "Карьер_эталон.xlsx"
SVOD = "№1ПС_Сводная"
ROAD_NAME = f"Демо — Автодорога — {DATE}"
KARIER_NAME = f"Демо — Карьер — {DATE}"
LABELS = {
    "geology": "Геология", "geophysics": "Геофизика", "geodesy": "Геодезия",
    "ecology": "Экология", "hydromet": "Гидрометеорология",
}


@dataclass
class Check:
    """Одна сверка: что сравнивается, итог программы, эталон, допуск."""

    title: str
    value: float
    expected: float
    rel: float | None = None   # относительный допуск (1e-3 = 0,1 %)
    abs_: float | None = None  # абсолютный допуск, руб.
    cell: str = ""

    @property
    def ok(self) -> bool:
        diff = abs(self.value - self.expected)
        if self.abs_ is not None and diff <= self.abs_:
            return True
        return self.rel is not None and diff <= self.rel * abs(self.expected)

    @property
    def deviation(self) -> float:
        return (self.value - self.expected) / self.expected if self.expected else 0.0


def _num(ws, addr) -> float:
    value = ws[addr].value
    return 0.0 if value is None or isinstance(value, str) else float(value)


# --- ввод объектов --------------------------------------------------------------


def road_object(wb):
    """Данные объекта «Автодорога»: общие ставки — как в листе геологии
    «1.2.1_ИГИ», у остальных разделов — свои ставки их листов (те же, что
    задают их эталонные тесты): геофизика — внешний транспорт 6,5 % и районный
    0,66 (№1.2.2_ИГФИ G29, G32); геодезия — районный к полевым 0,15 + 0,15
    (№1.1_ИГДИ G68); экология — районный к полевым 0,6, оргликвидация без ×2,5,
    расстояние для внутреннего транспорта 22 км (п.5 т.4). Снижение цены — как в
    сводной: ИГМИ × 0,65 (№1.3_ИГМИ D370), сводная × 0,8 (C36)."""
    from tests.test_ecology_etalons import SHEET_RATES
    from tests.test_geodesy_etalon import ROAD_OBJECT
    from tests.test_geology_etalon import sheet_input

    geology = sheet_input(wb["1.2.1_ИГИ"])[3]
    svod = wb[SVOD]
    return dataclasses.replace(
        geology, code="Т-01", name="Автодорога",
        transport_distance_overrides={"ecology": 22},
        accrual_overrides={
            "geophysics": {"external_transport_rate": 0.065, "regional_field_rate": 0.66},
            "geodesy": {name: getattr(ROAD_OBJECT, name) for name in (
                "external_transport_rate", "org_liquidation_rate", "org_liquidation_k",
                "regional_field_rate", "regional_office_rate")},
            "ecology": {**SHEET_RATES, "org_liquidation_k": 1.0},
        },
        price_reductions={"hydromet": 0.65, "budget": svod["C36"].value},
    )


def karier_object(wb):
    """Данные объекта «Карьер» — как в tests/test_karer_etalon.py (общие ставки
    листов ИГИ, свои — у геодезии и экологии), шифр условный «Т-02»; снижение
    сводной × 0,85 (C39)."""
    from tests.test_karer_etalon import karier_object as karier

    return dataclasses.replace(
        karier(wb), code="Т-02", name="Карьер",
        price_reductions={"budget": wb[SVOD]["C39"].value},
    )


def road_sections(wb):
    """{раздел: (модуль, ввод ведомости, ввод сметы, правки ведомости, правки сметы,
    свои строки)} — ввод эталонных тестов Автодороги."""
    from modules.ecology.module import EcologyModule
    from modules.geodesy.module import GeodesyModule
    from modules.geology.module import GeologyModule
    from modules.geophysics.module import GeophysicsModule
    from modules.hydromet import module as h
    from tests import (
        test_geodesy_etalon as geodesy,
        test_geophysics_etalon as geophysics,
        test_hydromet_etalons as hydromet,
    )
    from tests.test_ecology_etalons import ROAD_FULL_INPUT
    from tests.test_geology_etalon import sheet_input
    from tests.test_karer_etalon import ecology_customs

    volume_input, sheet_volumes, estimate_input, _obj = sheet_input(wb["1.2.1_ИГИ"])
    ecology_volumes, ecology_estimate = _split_ecology(EcologyModule(), ROAD_FULL_INPUT)
    return {
        "geology": (GeologyModule(), volume_input, estimate_input,
                    {key: ("qty", qty) for key, qty in sheet_volumes.items()}, None, ()),
        "geophysics": (GeophysicsModule(), geophysics.VOLUMES, geophysics.PARAMS, None, None, ()),
        "geodesy": (GeodesyModule(), geodesy.ROAD_VOLUMES, geodesy.ROAD_PARAMETERS, None, None, ()),
        # блок прайсов листа «№1.4_ИЭИ» — строки 97–113 (своими строками после индекса)
        "ecology": (EcologyModule(), ecology_volumes, ecology_estimate, None, None,
                    ecology_customs(wb["№1.4_ИЭИ"], range(97, 114))),
        "hydromet": (h.HydrometModule(), hydromet.ROAD_VOLUMES,
                     {**hydromet.ROAD_PARAMETERS, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_OFF},
                     None, None, ()),
    }


def karier_sections(wb):
    """Разделы Карьера — ввод tests/test_karer_etalon.py; геология — одна смета на
    три листа ИГИ с суммой объёмов. Геофизики в эталоне Карьера нет."""
    from modules.ecology.module import EcologyModule
    from modules.geodesy.module import GeodesyModule
    from modules.geology.module import GeologyModule
    from modules.hydromet import module as h
    from tests.test_ecology_etalons import KARIER_FULL_INPUT
    from tests.test_hydromet_etalons import KARIER_PARAMETERS, KARIER_VOLUMES
    from tests.test_karer_etalon import ecology_customs, geodesy_input, summed_geology_input

    volume_input, volume_edits, estimate_input, estimate_edits = summed_geology_input(wb)
    geodesy_volumes, geodesy_estimate, geodesy_edits = geodesy_input(wb["№2.1_ИГДИ"])
    ecology_volumes, ecology_estimate = _split_ecology(EcologyModule(), KARIER_FULL_INPUT)
    return {
        "geology": (GeologyModule(), volume_input, estimate_input, volume_edits, estimate_edits, ()),
        "geodesy": (GeodesyModule(), geodesy_volumes, geodesy_estimate, None, geodesy_edits, ()),
        "ecology": (EcologyModule(), ecology_volumes, ecology_estimate, None, None,
                    ecology_customs(wb["№2.5_ИЭИ"])),
        "hydromet": (h.HydrometModule(), KARIER_VOLUMES,
                     {**KARIER_PARAMETERS, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_OFF}, None, None, ()),
    }


def _module_class(code):
    from modules.ecology.module import EcologyModule
    from modules.geodesy.module import GeodesyModule
    from modules.geology.module import GeologyModule
    from modules.geophysics.module import GeophysicsModule
    from modules.hydromet.module import HydrometModule

    return {"geology": GeologyModule, "geophysics": GeophysicsModule, "geodesy": GeodesyModule,
            "ecology": EcologyModule, "hydromet": HydrometModule}[code]


def _split_ecology(module, full_input):
    """Ввод экологии — по этапам (параметры расценки — на этапе сметы)."""
    from core.stages import as_staged

    stages = as_staged(module).stages
    return tuple(
        {f.name: full_input[f.name] for f in stage.fields if f.name in full_input}
        for stage in stages
    )


# --- запись базы ------------------------------------------------------------------


def build_demo(db_path: Path | str = DEFAULT_DB, book=None) -> dict[str, dict[str, int]]:
    """Пересоздаёт базу `db_path` и записывает оба объекта. Возвращает
    {имя расчёта: {раздел: id версии сметы}}."""
    import openpyxl

    from core.persistence import SmetaStore
    from core.references import ReferenceBook
    from tests.test_karer_etalon import staged_session

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    store = SmetaStore(db_path)
    book = book or ReferenceBook()
    road, karier = (openpyxl.load_workbook(p, data_only=True) for p in (ROAD_XLSX, KARIER_XLSX))
    objects = (
        (ROAD_NAME, road_object(road), road_sections(road)),
        (KARIER_NAME, karier_object(karier), karier_sections(karier)),
    )
    saved: dict[str, dict[str, int]] = {}
    for name, obj, sections in objects:
        saved[name] = {}
        for code, (module, volumes, estimate, volume_edits, estimate_edits, customs) in sections.items():
            session, _context, _warnings = staged_session(
                module, obj, book, volumes, estimate, volume_edits, estimate_edits, customs,
                reason="как в эталонном листе")
            parent = None
            for stage in session.staged.stages:
                parent = store.save_version(name, code, parent, session.result(stage.key),
                                            calc_date=DATE, object_data=obj.to_dict())
            saved[name][code] = parent
    return saved


# --- сверка ---------------------------------------------------------------------


@dataclass
class SectionRow:
    code: str
    version_id: int
    base_total: float
    indexed_total: float
    total_with_vat: float
    reduced_total: float   # после снижения раздела, без НДС — столбец «до снижения» сводной


def read_back(db_path: Path | str, name: str, book=None):
    """Открывает последнюю версию сметы каждого раздела расчёта `name` так же,
    как интерфейс: цепочка версий → сессия → пересчёт на данных объекта
    версии. Возвращает (строки разделов, данные объекта, ошибки)."""
    from core.object_data import CalculationContext, ObjectData
    from core.persistence import SmetaStore
    from core.pricing import calculate_total_with_vat
    from core.references import ReferenceBook
    from core.stages import STATUS_CONFIRMED, StagedSession, as_staged, fingerprint
    store = SmetaStore(db_path)
    book = book or ReferenceBook()
    latest: dict[str, int] = {}
    for version_id, _parent, version_name, code, stage_key, _at in store.list_versions():
        if version_name == name and stage_key == "estimate" and code not in latest:
            latest[code] = version_id
    rows, errors, obj = [], [], ObjectData()
    for code in LABELS:
        if code not in latest:
            continue
        version_id = latest[code]
        module_code, _name, results = store.load_version_chain(version_id)
        obj = ObjectData.from_dict(store.get_version_object_data(version_id))
        calc_date = store.get_version_calc_date(version_id)
        context = CalculationContext(book, obj)
        session = StagedSession(as_staged(_module_class(module_code)()))
        session.restore(results)
        if [r.stage_key for r in results] != [s.key for s in session.staged.stages]:
            errors.append(f"{LABELS[code]}: в цепочке не оба этапа")
        for stage in session.staged.stages:
            if session.status(stage.key) != STATUS_CONFIRMED:
                errors.append(f"{LABELS[code]}: этап «{stage.title}» не подтверждён")
            elif fingerprint(session.compute(stage.key, context).effective) != \
                    session.result(stage.key).fingerprint:
                errors.append(f"{LABELS[code]}: этап «{stage.title}» пересчитывается иначе")
        totals = calculate_total_with_vat(dict(results[-1].lines), context, code, calc_date)
        if totals.warnings or totals.total_with_vat is None:
            errors.append(f"{LABELS[code]}: итог с предупреждениями — {totals.warnings}")
        reduced = round(totals.indexed_total * obj.reduction(code), 2)
        rows.append(SectionRow(code, version_id, totals.base_total, totals.indexed_total,
                               totals.total_with_vat, reduced))
    return rows, obj, errors


def _budget(rows, obj, book, db_path, name, with_budget_reduction):
    """Сводная из открытых версий — тем же `BudgetAggregator`, что страница 5."""
    from core.aggregator import BudgetAggregator, ModuleBudget
    from core.object_data import CalculationContext
    from core.persistence import SmetaStore
    from core.pricing import calculate_total_with_vat

    store = SmetaStore(db_path)
    aggregator = BudgetAggregator(
        budget_reduction=obj.reduction("budget") if with_budget_reduction else 1.0)
    context = CalculationContext(book, obj)
    for row in rows:
        _code, _name, results = store.load_version_chain(row.version_id)
        totals = calculate_total_with_vat(dict(results[-1].lines), context, row.code, DATE)
        aggregator.add_module(ModuleBudget.from_totals(
            row.code, LABELS[row.code], totals, reduction=obj.reduction(row.code)))
    return aggregator.summary()


def expected_checks(db_path, book=None) -> tuple[dict[str, list[Check]], dict[str, dict], list[str]]:
    """Сверки демо-базы с эталонами (допуски — как в эталонных тестах сводной:
    tests/test_aggregator.py, tests/test_karer_etalon.py). Возвращает
    ({имя расчёта: сверки}, {имя расчёта: сводная после снижения}, ошибки)."""
    import openpyxl

    from core.references import ReferenceBook
    from tests.test_karer_etalon import GEOLOGY_SHEETS, sheet_error_h143

    book = book or ReferenceBook()
    road = openpyxl.load_workbook(ROAD_XLSX, data_only=True)
    karier = openpyxl.load_workbook(KARIER_XLSX, data_only=True)
    checks: dict[str, list[Check]] = {}
    budgets: dict[str, dict] = {}
    errors: list[str] = []

    # Автодорога: D19…D23 — разделы (ИГМИ — после снижения 0,65), D18 — раздел
    # «Инженерные изыскания», G18 — после коэффициента сводной 0,8.
    rows, obj, errs = read_back(db_path, ROAD_NAME, book)
    errors += errs
    ws = road[SVOD]
    by_code = {r.code: r for r in rows}
    cells = {"geodesy": "D19", "geology": "D20", "geophysics": "D21", "hydromet": "D22",
             "ecology": "D23"}
    exact = {"hydromet", "ecology"}
    road_checks = [
        Check(LABELS[code], by_code[code].reduced_total, ws[cell].value, rel=1e-3,
              abs_=0.01 if code in exact else None, cell=cell)
        for code, cell in cells.items() if code in by_code
    ]
    if set(by_code) != set(cells):
        errors.append(f"Автодорога: разделы {sorted(by_code)} вместо {sorted(cells)}")
    before = _budget(rows, obj, book, db_path, ROAD_NAME, False)
    after = _budget(rows, obj, book, db_path, ROAD_NAME, True)
    road_checks += [
        Check("Инженерные изыскания", before["sections_total"], ws["D18"].value, rel=1e-3, cell="D18"),
        Check(f"После снижения сводной ×{obj.reduction('budget'):g}", after["reduced_total"],
              ws["G18"].value, rel=1e-3, cell="G18"),
    ]
    checks[ROAD_NAME], budgets[ROAD_NAME] = road_checks, after

    # Карьер: D25 геодезия, D26–D28 три листа ИГИ (одна смета — их сумма за
    # вычетом объяснённой ячейки H143 каждого листа), D29 ИГМИ, D30 экология,
    # D24 — раздел «Инженерные изыскания», X24 — после коэффициента 0,85.
    rows, obj, errs = read_back(db_path, KARIER_NAME, book)
    errors += errs
    ws = karier[SVOD]
    by_code = {r.code: r for r in rows}
    expected = {"geodesy": ("D25", ws["D25"].value), "hydromet": ("D29", ws["D29"].value),
                "ecology": ("D30", ws["D30"].value)}
    if "geology" in by_code:
        index = by_code["geology"].indexed_total / by_code["geology"].base_total
        error = sum(sheet_error_h143(karier[s]) for s in GEOLOGY_SHEETS) * index
        expected["geology"] = ("D26:D28 − H143", sum(_num(ws, f"D{r}") for r in (26, 27, 28)) - error)
    karier_checks = [
        Check(LABELS[code], by_code[code].reduced_total, value, rel=1e-3,
              abs_=0.01 if code in exact else None, cell=cell)
        for code in LABELS if code in expected and code in by_code
        for cell, value in [expected[code]]
    ]
    if set(by_code) != set(expected):
        errors.append(f"Карьер: разделы {sorted(by_code)} вместо {sorted(expected)}")
    before = _budget(rows, obj, book, db_path, KARIER_NAME, False)
    after = _budget(rows, obj, book, db_path, KARIER_NAME, True)
    karier_checks += [
        Check("Инженерные изыскания", before["sections_total"], ws["D24"].value, rel=1e-3, cell="D24"),
        Check(f"После снижения сводной ×{obj.reduction('budget'):g}", after["reduced_total"],
              ws["X24"].value, rel=1e-3, cell="X24"),
    ]
    checks[KARIER_NAME], budgets[KARIER_NAME] = karier_checks, after
    errors += [f"{name}: {c.title} — {c.value:,.2f} при эталоне {c.expected:,.2f} ({c.cell})"
               for name, items in checks.items() for c in items if not c.ok]
    return checks, budgets, errors


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def report(checks, budgets) -> str:
    lines = []
    for name, items in checks.items():
        lines.append(f"\n{name}")
        lines.append(f"{'Сверка':<34}{'Программа':>18}{'Эталон':>18}  {'Ячейка':<16}{'Откл.':>10}")
        for c in items:
            mark = "" if c.ok else "  ← сверх допуска"
            lines.append(f"{c.title:<34}{_money(c.value):>18}{_money(c.expected):>18}  "
                         f"{c.cell:<16}{c.deviation * 100:>+9.4f}%{mark}")
        budget = budgets[name]
        lines.append(f"Сводная: без НДС {_money(budget['reduced_total'])}, НДС "
                     f"{_money(budget['vat_amount_sum'])}, с НДС {_money(budget['total_with_vat_sum'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Демо-база сохранённых смет (два объекта).")
    parser.add_argument("db_path", nargs="?", default=str(DEFAULT_DB),
                        help=f"путь к базе (по умолчанию {DEFAULT_DB.relative_to(ROOT)})")
    args = parser.parse_args(argv)
    for path in (ROAD_XLSX, KARIER_XLSX):
        if not path.exists():
            print(f"Нет эталона: {path}")
            return 1
    from core.references import DEFAULT_DB_PATH, ReferenceBook

    if not DEFAULT_DB_PATH.exists():
        print("Нет справочника data/references.sqlite — соберите: data/build_references.py")
        return 1
    book = ReferenceBook()
    saved = build_demo(args.db_path, book)
    print(f"База записана: {args.db_path}")
    for name, versions in saved.items():
        print(f"  {name}: " + ", ".join(f"{LABELS[c]} #{v}" for c, v in versions.items()))
    checks, budgets, errors = expected_checks(args.db_path, book)
    print(report(checks, budgets))
    if errors:
        print("\nОШИБКИ СВЕРКИ:\n  " + "\n  ".join(errors))
        return 1
    print("\nСверка с эталонами — в допусках.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
