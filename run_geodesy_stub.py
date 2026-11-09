"""Ходячий скелет геодезии: ввод -> ведомость объёмов -> смета.

По образцу run_stub.py/run_geophysics_stub.py. Ввод захардкожен как
EXAMPLE_INPUT (8 пунктов опорной сети, 50 точек привязки выработок —
те же объёмы, что в эталонном листе №1.1_ИГДИ, строки 21 и 35), т.к.
скрипту неоткуда взять живой ввод оператора (для геодезии ещё нет
Streamlit-формы — Этап 7 намеренно ограничен модулем+справочником,
как и Этап 6 у геофизики)."""

from datetime import date

from core.export_excel import export_to_excel
from core.models import calculate_amount
from core.pricing import calculate_total_with_vat
from core.references import ReferenceBook
from modules.geodesy.module import GeodesyModule

EXAMPLE_INPUT = {"oporny_points_count": 8, "privyazka_count": 50}


def main() -> None:
    module = GeodesyModule()
    refs = ReferenceBook()
    volumes = module.build_volumes(EXAMPLE_INPUT, refs=refs)
    estimate = module.build_estimate(volumes, refs=refs)

    cache: dict[str, float] = {}

    print("Ведомость объёмов:")
    for key, qty in volumes.items():
        print(f"  {key}: {qty}")

    print("\nСмета:")
    for line in estimate.values():
        amount = calculate_amount(line, estimate, cache)
        print(
            f"  {line.id:<22} {line.name:<55} "
            f"{line.qty:>8.3f} {line.unit:<18} × {line.price:>8.2f} = {amount:>12.2f} руб."
        )
        print(f"      основание: {line.basis}")

    print()
    for warning in module.check(estimate):
        print(f"Внимание: {warning}")

    output_path = "output_geodesy.xlsx"
    export_to_excel(estimate, output_path, cache)

    today = date.today().isoformat()
    totals = calculate_total_with_vat(estimate, refs, work_type=module.code, date=today, cache=cache)
    for warning in totals.warnings:
        print(f"Внимание: {warning}")

    print(
        f"\nГотово: {output_path}, {len(estimate)} строк, "
        f"сумма в базовых ценах (2004 г.): {totals.base_total:.2f} руб."
    )
    if totals.total_with_vat is not None:
        print(
            f"Итого на {today} с индексацией (×{totals.index}) и НДС "
            f"({totals.vat_rate:.0%}): {totals.total_with_vat:.2f} руб."
        )
    else:
        print(
            f"Итог с индексацией и НДС на {today} недоступен "
            "(см. предупреждения выше) — показана только сумма в базовых ценах."
        )


if __name__ == "__main__":
    main()
