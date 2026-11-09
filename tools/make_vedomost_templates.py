"""Шаблоны Excel «Ведомость объёмов работ» пяти разделов (03.11.2026).

Отдел заполняет шаблон своего раздела и загружает его на странице «2. Ведомость
объёмов» (блок «Ведомость из Excel»); чтение и проверка — `core/vedomost_import.py`,
устройство книги — `core/vedomost_template.py`. Перечень видов работ выводится из
полей этапа 1 модуля (категории грунта геологии — из справочника
`data/references.sqlite`), руками не набирается.

Запуск (из корня проекта):
    .venv/Scripts/python.exe -m tools.make_vedomost_templates          # docs/templates/vedomost_*.xlsx
    .venv/Scripts/python.exe -m tools.make_vedomost_templates --check  # сверить шаблоны в git с кодом
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openpyxl import load_workbook

from core.references import ReferenceBook
from core.vedomost_import import build_catalog
from core.vedomost_template import SECTION_TITLES, build_workbook, template_file_name
from modules.ecology.module import EcologyModule
from modules.geodesy.module import GeodesyModule
from modules.geology.module import GeologyModule
from modules.geophysics.module import GeophysicsModule
from modules.hydromet.module import HydrometModule

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "docs" / "templates"

# Код раздела → (класс модуля, название раздела в шаблоне).
_CLASSES = {
    "geology": GeologyModule,
    "geophysics": GeophysicsModule,
    "geodesy": GeodesyModule,
    "ecology": EcologyModule,
    "hydromet": HydrometModule,
}
SECTIONS = {code: (cls, SECTION_TITLES[code]) for code, cls in _CLASSES.items()}


def template_path(code: str, directory: Path = TEMPLATES_DIR) -> Path:
    return directory / template_file_name(code)


def build_template(code: str, refs=None):
    module_class, title = SECTIONS[code]
    refs = refs if refs is not None else ReferenceBook()
    return build_workbook(build_catalog(module_class(), refs, code), title)


def sheet_values(wb) -> dict[str, list[list]]:
    """Содержимое книги для сравнения: значения ячеек всех листов, формулы
    проверки данных и защита."""
    result = {}
    for ws in wb.worksheets:
        values = [list(row) for row in ws.iter_rows(values_only=True)]
        validations = sorted((dv.type or "", dv.formula1 or "", str(dv.sqref))
                             for dv in ws.data_validations.dataValidation)
        result[ws.title] = [values, validations, bool(ws.protection.sheet)]
    return result


def stale_templates(directory: Path = TEMPLATES_DIR, refs=None) -> list[str]:
    """Коды разделов, у которых шаблон в папке не совпадает с генератором (или нет файла)."""
    refs = refs if refs is not None else ReferenceBook()
    stale = []
    for code in SECTIONS:
        path = template_path(code, directory)
        if not path.exists() or sheet_values(load_workbook(path)) != sheet_values(build_template(code, refs)):
            stale.append(code)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Шаблоны Excel «Ведомость объёмов работ»")
    parser.add_argument("--out", type=Path, default=TEMPLATES_DIR, help="папка для шаблонов")
    parser.add_argument("--check", action="store_true", help="только сверить шаблоны с кодом")
    args = parser.parse_args(argv)
    refs = ReferenceBook()
    if args.check:
        stale = stale_templates(args.out, refs)
        if stale:
            print("Шаблоны устарели: " + ", ".join(stale)
                  + ". Пересоздайте: python -m tools.make_vedomost_templates")
            return 1
        print("Шаблоны ведомости совпадают с кодом.")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for code in SECTIONS:
        path = template_path(code, args.out)
        build_template(code, refs).save(path)
        print(f"Шаблон сохранён: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
