"""Ходячий скелет сборщика бюджета: смета геологии + геофизики +
геодезии -> сводный бюджет объекта.

По образцу run_stub.py/run_geophysics_stub.py/run_geodesy_stub.py:
считает все три сметы с теми же захардкоженными входными данными, что
и в этих скриптах, и объединяет их в BudgetAggregator.

Геология и геофизика считаются от базы цен 1991 г. (индекс ~82),
геодезия — от базы 2004 г. (индекс ~7, см. записи разработки).
BudgetAggregator НЕ пытается сравнивать индексы модулей между собой
или приводить их к общей базе — просто складывает уже готовые
total_with_vat каждого модуля (это уже реальные текущие рубли на дату
расчёта, индексация каждого модуля сама по себе переводит из СВОЕЙ
базы в текущие — см. core/aggregator.py::BudgetAggregator.summary).
Единственная сумма, которую НЕЛЬЗЯ интерпретировать через разные базы
буквально как "одну валюту" — сумма base_total ДО индексации (это
разные по факту "рубли", 1991 и 2004 года), но она всё равно
считается — просто это не итоговая сумма проекта, а справочная.

Все пять модулей считаются через этапы (core/stages.py, см.
add_staged_module): ведомость объёмов подтверждается, смета строится
от её снимка; в бюджет модуль попадает только с подтверждённым и не
устаревшим последним этапом. Итоговые цифры те же, что при прежнем
прямом вызове build_volumes → build_estimate (сверено diff-ом вывода,
записи разработки)."""

import argparse
from datetime import date

from core.aggregator import BudgetAggregator, ModuleBudget
from core.pricing import INDEX_POLICY_LAST_KNOWN, INDEX_POLICY_STRICT, calculate_total_with_vat
from core.references import ReferenceBook
from core.stages import STATUS_DRAFT, STATUS_STALE, StagedSession, as_staged
from modules.ecology.module import EcologyModule
from modules.geodesy.module import GeodesyModule
from modules.geology.module import GeologyModule
from modules.geophysics.module import GeophysicsModule
from modules.hydromet.module import HydrometModule

GEOLOGY_INPUT = {
    "drilling_by_category": [{"soil_category": "IV", "drilling_length": 450}],
    "drilling_count": 30,
}
GEOPHYSICS_INPUT = {"vez_count": 80, "mpv_count": 400}
GEODESY_INPUT = {"oporny_points_count": 8, "privyazka_count": 50}
ECOLOGY_INPUT = {
    "radiation_area": 760,
    "radon_points": 200,
    "physical_factors_points": 10,
    "sampling": [
        {"sample_type": "soil_chemical", "sample_count": 160},
        {"sample_type": "surface_water_chemical", "sample_count": 3},
    ],
    "data_acquisition_count": 26,
}
# Гидрометеорология — объёмы и параметры сметы эталона «№2.4_ИГМИ»
# (Карьер), см. tests/test_hydromet_etalons.py: база 14 958 руб.
HYDROMET_VOLUMES = {
    "recon_route_km": 3, "recon_river_km": 1, "morphometric_count": 1, "morpho_stvor_km": 1,
    "gvv_count": 2, "slope_levelling_km": 2, "sounding_stvor_count": 2,
    "flow_measurements": 2, "depth_soundings": 2, "photo_count": 10,
    "study_table_count": 1, "study_scheme_count": 1, "flow_graphs_count": 2,
    "rating_curve_count": 2, "basin_area_count": 2, "basin_slope_count": 2,
    "max_flow_first": 1, "max_flow_next": 1, "spring_flood_first": 1, "spring_flood_next": 1,
    "min_flow_first": 1, "min_flow_next": 1, "note_channel_count": 1, "note_ice_count": 1,
    "note_climate_count": 1, "station_selection_count": 1,
}
HYDROMET_PARAMETERS = {
    "complexity_category": "III", "field_regional_coefficient": 1.85, "river_width": "до 20 м",
    "ice_note_item": "7", "program_price": 800, "internal_transport_rate": "0.1875",
    "external_transport_rate": 0.364, "distance_km": 5000,
}


def add_staged_module(aggregator, module, name, tag, inputs_by_stage, refs, today,
                      index_policy=INDEX_POLICY_LAST_KNOWN) -> None:
    """Проходит все этапы модуля с заданным вводом (`{stage_key: ввод}`),
    подтверждая каждый, и добавляет итог последнего этапа в бюджет."""
    session = StagedSession(as_staged(module))
    result = None
    for stage in session.staged.stages:
        session.inputs[stage.key] = inputs_by_stage.get(stage.key, {})
        result = session.confirm(stage.key, refs)
    totals = calculate_total_with_vat(
        dict(result.lines), refs, work_type=module.code, date=today, index_policy=index_policy
    )
    for warning in totals.warnings:
        print(f"[{tag}] Внимание: {warning}")
    status = session.last_stage_status()
    aggregator.add_module(
        ModuleBudget.from_totals(
            module.code, name, totals,
            confirmed=status != STATUS_DRAFT,
            stale=status == STATUS_STALE,
        )
    )


def build_budget(aggregator, refs, today, policy=INDEX_POLICY_LAST_KNOWN) -> BudgetAggregator:
    """Все пять модулей с данными этого скрипта — в `aggregator` (его же
    и возвращает). Отдельно от `main`, чтобы тест мог сверить итог
    на дату (контрольная цифра 28 680 880,97 на 2026-09-30, НДС 22 %)."""
    # Многоэтапные модули (core/stages.py): каждый этап подтверждается
    # по порядку, в бюджет модуль попадает только с подтверждённым и не
    # устаревшим последним этапом.
    add_staged_module(aggregator, GeologyModule(), "Геология (ОИГ)", "геология",
                      {"volumes": GEOLOGY_INPUT}, refs, today, policy)
    add_staged_module(aggregator, GeophysicsModule(), "Геофизика (ИГФИ)", "геофизика",
                      {"volumes": GEOPHYSICS_INPUT}, refs, today, policy)
    add_staged_module(aggregator, GeodesyModule(), "Геодезия (ИГДИ)", "геодезия",
                      {"volumes": GEODESY_INPUT}, refs, today, policy)
    # У экологии ECOLOGY_INPUT содержит только объёмы; параметры сметы
    # (пункт Т80, проходимость, коэффициенты, итог лабораторных) не
    # заданы — берутся значения по умолчанию, как и раньше.
    add_staged_module(aggregator, EcologyModule(), "Экология (ИЭИ)", "экология",
                      {"volumes": ECOLOGY_INPUT}, refs, today, policy)
    add_staged_module(aggregator, HydrometModule(), "Гидрометеорология (ИГМИ)", "гидрометеорология",
                      {"volumes": HYDROMET_VOLUMES, "estimate": HYDROMET_PARAMETERS}, refs, today, policy)
    return aggregator


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сквозной прогон пяти модулей и сводный бюджет")
    parser.add_argument(
        "--date", default=None,
        help="дата расчёта ГГГГ-ММ-ДД (по ней берётся индекс пересчёта цен); по умолчанию сегодня",
    )
    parser.add_argument(
        "--strict-index", action="store_true",
        help="строгий режим: нет индекса на дату — нет итога с индексацией (иначе — "
        "последний известный индекс с предупреждением)",
    )
    args = parser.parse_args(argv)
    if args.date is not None:
        date.fromisoformat(args.date)  # ValueError на неверный формат
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    refs = ReferenceBook()
    today = args.date or date.today().isoformat()
    policy = INDEX_POLICY_STRICT if args.strict_index else INDEX_POLICY_LAST_KNOWN
    aggregator = BudgetAggregator()
    print(f"Дата расчёта: {today}; политика индекса: {policy}")

    build_budget(aggregator, refs, today, policy)

    summary = aggregator.summary()

    print("\nСводный бюджет:")
    for module_budget in summary["modules"]:
        print(f"  {module_budget.module_name}:")
        print(f"    база (в ценах модуля): {module_budget.base_total:>14.2f} руб.")
        print(
            "    с индексацией:        "
            f"{'—' if module_budget.indexed_total is None else f'{module_budget.indexed_total:>14.2f}'} руб."
        )
        print(
            "    НДС:                  "
            f"{'—' if module_budget.vat_amount is None else f'{module_budget.vat_amount:>14.2f}'} руб."
        )
        print(
            "    итого с НДС:          "
            f"{'—' if module_budget.total_with_vat is None else f'{module_budget.total_with_vat:>14.2f}'} руб."
        )

    # Сумма баз разделов не печатается: базы разных лет (1991, 2004, 2000 гг.) не складываются
    # (аудит 05.11.2026, L-Б7); итог объекта — только в текущих ценах.
    print(f"\nСумма 'итого с НДС' по посчитанным модулям: {summary['total_with_vat_sum']:.2f} руб.")
    for vat_label in aggregator.vat_labels():
        print(f"  {vat_label}")
    with_warnings = [
        m for m in summary["modules"] if m.total_with_vat is not None and m.warnings
    ]
    if with_warnings:
        print("\nВНИМАНИЕ — итог посчитан с предупреждениями:")
        for module_budget in with_warnings:
            for warning in module_budget.warnings:
                print(f"  {module_budget.module_name}: {warning}")

    if summary["excluded_from_total"]:
        print("\nНе вошли в сумму 'итого с НДС':")
        for excluded in summary["excluded_from_total"]:
            print(f"  {excluded['module_name']} ({excluded['module_code']}):")
            for warning in excluded["warnings"]:
                print(f"    {warning}")

    output_path = "budget_output.xlsx"
    aggregator.to_excel(output_path, info={"Дата расчёта": today, "Политика индекса": policy})
    print(f"\nГотово: {output_path}")


if __name__ == "__main__":
    main()
