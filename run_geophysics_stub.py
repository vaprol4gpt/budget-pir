"""Ходячий скелет геофизики: ввод -> ведомость объёмов -> смета.

По образцу run_stub.py (геология). Ввод захардкожен как
EXAMPLE_INPUT (80 физ.точек ВЭЗ, 400 физ.наблюдений МПВ — те же
объёмы, что в эталонном листе №1.2.2_ИГФИ), т.к. скрипту неоткуда
взять живой ввод оператора (для геофизики ещё нет Streamlit-формы —
см. описание проекта, Этап 6 намеренно ограничен модулем+справочником)."""

from core.export_excel import export_to_excel
from core.models import calculate_amount
from core.references import ReferenceBook
from modules.geophysics.module import GeophysicsModule

EXAMPLE_INPUT = {"vez_count": 80, "mpv_count": 400}


def main() -> None:
    module = GeophysicsModule()
    refs = ReferenceBook()
    volumes = module.build_volumes(EXAMPLE_INPUT, refs=refs)
    estimate = module.build_estimate(volumes, refs=refs)

    cache: dict[str, float] = {}

    print("Ведомость объёмов:")
    for key, qty in volumes.items():
        print(f"  {key}: {qty}")

    print("\nСмета:")
    total = 0.0
    for line in estimate.values():
        amount = calculate_amount(line, estimate, cache)
        total += amount
        print(
            f"  {line.id:<26} {line.name:<45} "
            f"{line.qty:>8.3f} {line.unit:<6} × {line.price:>8.4f} = {amount:>12.2f} руб."
        )
        print(f"      основание: {line.basis}")

    print()
    for warning in module.check(estimate):
        print(f"Внимание: {warning}")

    output_path = "output_geophysics.xlsx"
    export_to_excel(estimate, output_path, cache)

    print(f"\nГотово: {output_path}, {len(estimate)} строк, сумма в ценах 1982 г.: {total:.2f} руб.")


if __name__ == "__main__":
    main()
