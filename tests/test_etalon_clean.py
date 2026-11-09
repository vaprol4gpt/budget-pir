"""Эталоны `reference_data/*.xlsx` без следов чужих файлов (аудит 10.11, Н-1).

В именах пользовательских стилей Excel (`xl/styles.xml`) копились имена файлов чужих смет, из которых
когда-то копировали листы; в двух ячейках — полное название исполнителя. В публичной копии стили
переименованы в «Стиль N», исполнитель — обезличен (`tools/make_public.py`): там тест должен проходить.
В рабочем репозитории эталоны по правилу проекта не правятся — там тест ожидаемо не проходит (xfail)."""

import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOKS = sorted((ROOT / "reference_data").glob("*.xlsx"))
WORKING_REPO = (ROOT / "tools" / "make_public.py").exists()  # в публичной копии сборщика нет

pytestmark = pytest.mark.skipif(not BOOKS, reason="нет эталонов в reference_data/")


def _custom_style_names(book: Path) -> list[str]:
    xml = zipfile.ZipFile(book).read("xl/styles.xml").decode("utf-8")
    names = []
    for tag in re.findall(r"<cellStyle\b[^>]*/>", xml):
        if "builtinId=" in tag:
            continue
        name = re.search(r'name="([^"]*)"', tag).group(1)
        if not re.fullmatch(r"Стиль \d+", name):
            names.append(name)
    return names


def _texts(book: Path) -> str:
    z = zipfile.ZipFile(book)
    return "\n".join(z.read(n).decode("utf-8", errors="ignore") for n in z.namelist() if n.endswith(".xml"))


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_etalon_has_no_custom_style_names_and_no_contractor_name(book):
    if WORKING_REPO:
        pytest.xfail("рабочие эталоны не перечищаются (правило проекта); чистые копии — в публичной копии")
    assert _custom_style_names(book) == []
    assert "ЗАО" not in _texts(book)  # организационная форма была только у исполнителя
