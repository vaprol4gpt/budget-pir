"""Неисправленные находки ревью 25.10 (С5, 30.10.2026): №10 — нулевые
объёмы предупреждаются; №4 — старые версии экологии с правкой цены пробы
открываются с понятным предупреждением и инструкцией (данные не мигрируют).
№8 (база в строке ИТОГО выгрузки) — tests/test_aggregator.py,
tests/test_review_fixes.py."""

import pytest

from core.module_helpers import zero_volume_warnings
from core.overrides import Edit
from core.stages import StagedSession, as_staged
from modules.ecology.module import EcologyModule
from modules.geodesy.module import GeodesyModule
from modules.geophysics.module import GeophysicsModule


def test_empty_section_gives_no_zero_warnings():
    assert zero_volume_warnings({"a": 0, "rows": [{"x": 0}]}, {"a": "А"}, {"rows": ("x", "Х")}) == []


def test_zero_field_in_non_empty_section_warns():
    warnings = zero_volume_warnings({"a": 0, "b": 5}, {"a": "Поле А", "b": "Поле Б"})
    assert len(warnings) == 1 and "«Поле А» = 0" in warnings[0]


def test_zero_in_group_row_warns_with_row_number():
    warnings = zero_volume_warnings(
        {"b": 1, "rows": [{"x": 2}, {"x": 0}]}, {}, {"rows": ("x", "Площадь")}
    )
    assert warnings == [warnings[0]] and "строке 2" in warnings[0]


@pytest.mark.parametrize("module, inputs, field", [
    (GeophysicsModule(), {"vez_count": 0, "mpv_count": 400}, "Объём ВЭЗ"),
    (GeodesyModule(), {"oporny_points_count": 8, "privyazka_count": 0}, "Число точек привязки"),
])
def test_volume_stage_warns_but_does_not_block(module, inputs, field):
    session = StagedSession(as_staged(module))
    session.inputs["volumes"] = inputs
    draft = session.compute("volumes", None)
    assert any(field in w for w in draft.warnings)
    session.confirm("volumes", None)  # не блокирует подтверждение
    assert session.result("volumes") is not None


def _result(stage_key, confirmed_at, edits):
    from types import SimpleNamespace

    return SimpleNamespace(stage_key=stage_key, confirmed_at=confirmed_at, edits=tuple(edits))


SAMPLING_PRICE = Edit("ecology.sampling.soil_chemical", "price", 10.0, 9.0)


def test_old_ecology_version_with_sampling_price_edit_warns():
    from app.ui import legacy_sampling_price_warning

    text = legacy_sampling_price_warning(
        "ecology", [_result("estimate", "2026-10-20T10:00:00", [SAMPLING_PRICE])]
    )
    assert text is not None and "ecology.sampling.soil_chemical" in text
    assert "подтвердите смету заново" in text


@pytest.mark.parametrize("module_code, results, draft_edits", [
    ("ecology", [_result("estimate", "2026-10-28T10:00:00", [SAMPLING_PRICE])], []),  # после переноса
    ("ecology", [_result("estimate", "2026-10-20T10:00:00",
                         [Edit("ecology.recon", "price", 1.0, 2.0)])], []),  # не проба
    ("geology", [_result("estimate", "2026-10-20T10:00:00", [SAMPLING_PRICE])], []),
])
def test_no_warning_when_not_affected(module_code, results, draft_edits):
    from app.ui import legacy_sampling_price_warning

    assert legacy_sampling_price_warning(module_code, results, draft_edits) is None


def test_draft_with_sampling_price_edit_warns():
    from app.ui import legacy_sampling_price_warning

    assert legacy_sampling_price_warning("ecology", [], draft_edits=[SAMPLING_PRICE]) is not None


def test_ecology_volume_stage_lists_zero_sample_rows():
    session = StagedSession(as_staged(EcologyModule()))
    session.inputs["volumes"] = {
        "recon_length_km": 4, "observation_points": 5, "radiation_area": 0, "radon_points": 0,
        "physical_factors_points": 0, "data_acquisition_count": 0,
        "sampling": [{"sample_type": "soil_chemical", "sample_count": 0}],
    }
    warnings = session.compute("volumes", None).warnings
    assert any("Количество проб" in w and "строке 1" in w for w in warnings)
