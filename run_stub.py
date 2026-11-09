"""Ходячий скелет: ввод -> ведомость объёмов -> смета -> Excel.

Пример ввода ниже (450 п.м., 30 скважин, категория IV) —
демонстрационный набор для показа полной ведомости из
docs/predmetnaya_oblast.md (классы А/Б/В); в отличие от
Streamlit-интерфейса, здесь ввод неоткуда взять от живого оператора,
поэтому он захардкожен."""

from datetime import date

from core.export_excel import export_to_excel
from core.models import calculate_amount
from core.pricing import calculate_total_with_vat
from core.references import ReferenceBook
from modules.geology.module import GeologyModule

EXAMPLE_INPUT = {"drilling_length": 450, "soil_category": "IV", "drilling_count": 30}


def main() -> None:
    module = GeologyModule()
    refs = ReferenceBook()
    volumes = module.build_volumes(EXAMPLE_INPUT, refs=refs)
    estimate = module.build_estimate(volumes, refs=refs)

    # Один кеш на весь расчёт этой сметы (печать + экспорт + индексация)
    # — см. core/models.py::calculate_amount и записи разработки.
    # Локален для этого вызова main(): не хранится нигде за его
    # пределами, не переиспользуется между разными сметами.
    cache: dict[str, float] = {}

    print("Ведомость объёмов:")
    for key, qty in volumes.items():
        print(f"  {key}: {qty}")

    print("\nСмета:")
    for line in estimate.values():
        amount = calculate_amount(line, estimate, cache)
        print(
            f"  {line.id:<28} {line.name:<55} "
            f"{line.qty:>10.3f} {line.unit:<10} × {line.price:>8.3f} = {amount:>12.2f} руб."
        )
        print(f"      основание: {line.basis}")

    print()
    for warning in module.check(estimate):
        print(f"Внимание: {warning}")

    output_path = "output.xlsx"
    export_to_excel(estimate, output_path, cache)

    today = date.today().isoformat()
    totals = calculate_total_with_vat(estimate, refs, work_type=module.code, date=today, cache=cache)
    for warning in totals.warnings:
        print(f"Внимание: {warning}")

    print(
        f"Готово: {output_path}, {len(estimate)} строк, "
        f"сумма в ценах 1991 г.: {totals.base_total:.2f} руб."
    )
    if totals.total_with_vat is not None:
        print(
            f"Итого на {today} с индексацией (×{totals.index}) и НДС "
            f"({totals.vat_rate:.0%}): {totals.total_with_vat:.2f} руб."
        )
    else:
        print(
            f"Итог с индексацией и НДС на {today} недоступен "
            "(см. предупреждения выше) — показана только сумма в ценах 1991 г."
        )


if __name__ == "__main__":
    main()
