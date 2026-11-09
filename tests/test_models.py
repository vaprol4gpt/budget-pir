import pytest

from core.models import EstimateLine, calculate_amount, excel_round


def make_line(**overrides) -> EstimateLine:
    defaults = dict(
        id="geology.drilling.cat3",
        name="Бурение скважин, кат. III",
        unit="п.м.",
        qty=100.0,
        price=250.0,
        coefs={},
        kind="A",
        base_ids=[],
        basis="СБЦ-99, табл. 9, п. 3",
    )
    defaults.update(overrides)
    return EstimateLine(**defaults)


def test_kind_a_without_coefs():
    line = make_line(qty=10.0, price=5.0, coefs={})
    assert calculate_amount(line, {}) == 50.0


def test_kind_a_multiplies_all_coefs():
    line = make_line(qty=100.0, price=250.0, coefs={"index": 81.95, "district": 1.25})
    assert calculate_amount(line, {}) == pytest.approx(2_560_937.5)


def test_kind_b_percent_of_single_base_line():
    drilling = make_line(qty=10.0, price=5.0, coefs={})
    transport = make_line(
        id="geology.transport",
        kind="B",
        base_ids=["geology.drilling.cat3"],
        coefs={"rate": 0.1},
    )
    lines = {line.id: line for line in [drilling, transport]}

    assert calculate_amount(transport, lines) == pytest.approx(5.0)


def test_kind_c_percent_of_several_base_lines():
    line_1 = make_line(id="a", qty=10.0, price=5.0)
    line_2 = make_line(id="b", qty=20.0, price=5.0)
    overhead = make_line(
        id="overhead", kind="C", base_ids=["a", "b"], coefs={"rate": 0.2}
    )
    lines = {line.id: line for line in [line_1, line_2, overhead]}

    # (50 + 100) * 0.2
    assert calculate_amount(overhead, lines) == pytest.approx(30.0)


def test_kind_b_can_reference_another_b_line():
    drilling = make_line(qty=10.0, price=5.0)
    transport = make_line(
        id="transport", kind="B", base_ids=["geology.drilling.cat3"], coefs={"rate": 0.1}
    )
    profit = make_line(
        id="profit", kind="B", base_ids=["geology.drilling.cat3", "transport"], coefs={"rate": 0.5}
    )
    lines = {line.id: line for line in [drilling, transport, profit]}

    # (50 + 5) * 0.5
    assert calculate_amount(profit, lines) == pytest.approx(27.5)


def test_kind_b_missing_rate_raises_value_error():
    line = make_line(kind="B", base_ids=[], coefs={})
    with pytest.raises(ValueError):
        calculate_amount(line, {})


def test_kind_b_missing_base_id_raises_key_error():
    line = make_line(kind="B", base_ids=["missing"], coefs={"rate": 0.1})
    with pytest.raises(KeyError):
        calculate_amount(line, {})


def test_unknown_kind_raises_value_error():
    line = make_line(kind="Z")
    with pytest.raises(ValueError):
        calculate_amount(line, {})


# --- защита от циклов в base_ids ---


def test_self_reference_raises_value_error_with_path():
    line = make_line(id="a", kind="B", base_ids=["a"], coefs={"rate": 1.0})
    lines = {"a": line}

    with pytest.raises(ValueError, match=r"a → a"):
        calculate_amount(line, lines)


def test_two_line_cycle_raises_value_error_with_full_path():
    a = make_line(id="a", kind="B", base_ids=["b"], coefs={"rate": 1.0})
    b = make_line(id="b", kind="B", base_ids=["a"], coefs={"rate": 1.0})
    lines = {"a": a, "b": b}

    with pytest.raises(ValueError, match=r"a → b → a"):
        calculate_amount(a, lines)


def test_three_line_cycle_raises_value_error_with_full_path():
    a = make_line(id="a", kind="B", base_ids=["b"], coefs={"rate": 1.0})
    b = make_line(id="b", kind="C", base_ids=["c"], coefs={"rate": 1.0})
    c = make_line(id="c", kind="B", base_ids=["a"], coefs={"rate": 1.0})
    lines = {"a": a, "b": b, "c": c}

    with pytest.raises(ValueError, match=r"a → b → c → a"):
        calculate_amount(a, lines)


def test_cycle_detection_does_not_false_positive_on_shared_non_cyclic_base():
    # "ромб": две строки ссылаются на ОДНУ и ту же базовую строку —
    # это не цикл, обе ветки рекурсии независимы и должны спокойно
    # посчитаться
    base = make_line(id="base", qty=10.0, price=5.0)
    left = make_line(id="left", kind="B", base_ids=["base"], coefs={"rate": 0.1})
    right = make_line(id="right", kind="B", base_ids=["base", "left"], coefs={"rate": 0.2})
    lines = {"base": base, "left": left, "right": right}

    # (50 + 5) * 0.2 = 11.0
    assert calculate_amount(right, lines) == pytest.approx(11.0)


# --- мемоизация (cache) ---


def test_cache_gives_same_result_as_without_cache():
    base = make_line(id="base", qty=10.0, price=5.0)
    dependent = make_line(id="dep", kind="B", base_ids=["base"], coefs={"rate": 0.5})
    lines = {"base": base, "dep": dependent}

    without_cache = calculate_amount(dependent, lines)
    with_cache = calculate_amount(dependent, lines, cache={})

    assert with_cache == pytest.approx(without_cache)
    assert with_cache == pytest.approx(25.0)


def test_cache_stores_every_computed_line_amount():
    base = make_line(id="base", qty=10.0, price=5.0)
    dependent = make_line(id="dep", kind="B", base_ids=["base"], coefs={"rate": 0.5})
    lines = {"base": base, "dep": dependent}

    cache: dict[str, float] = {}
    calculate_amount(dependent, lines, cache)

    assert cache["base"] == pytest.approx(50.0)
    assert cache["dep"] == pytest.approx(25.0)


def test_cache_prevents_recomputation_of_shared_base_line(monkeypatch):
    """Самое важное свойство мемоизации: если на одну и ту же базовую
    строку ссылаются несколько других строк (как programma_rabot,
    на которую ссылается и прямой перебор строк сметы, и техотчёт),
    она должна ПОЛНОСТЬЮ пересчитываться не более одного раза за весь
    проход по общему кешу — проверяем это счётчиком реальных вычислений
    (не просто вызовов функции — попадания в кеш не считаются)."""
    import core.models as models

    base = make_line(id="base", qty=10.0, price=5.0)
    dep1 = make_line(id="dep1", kind="B", base_ids=["base"], coefs={"rate": 0.1})
    dep2 = make_line(id="dep2", kind="B", base_ids=["base"], coefs={"rate": 0.2})
    lines = {"base": base, "dep1": dep1, "dep2": dep2}

    original = models.calculate_amount
    computed_counts: dict[str, int] = {}

    def counting(line, all_lines, cache=None, _visiting=()):
        cache_hit = cache is not None and line.id in cache
        if not cache_hit:
            computed_counts[line.id] = computed_counts.get(line.id, 0) + 1
        return original(line, all_lines, cache, _visiting)

    monkeypatch.setattr(models, "calculate_amount", counting)

    cache: dict[str, float] = {}
    for line in lines.values():
        # имитируем перебор всех строк одной сметы с общим кешем —
        # именно так вызывается calculate_amount в core/export_excel.py
        counting(line, lines, cache)

    assert computed_counts["base"] == 1  # не 3 (прямо + из dep1 + из dep2)
    assert cache["dep1"] == pytest.approx(5.0)
    assert cache["dep2"] == pytest.approx(10.0)


def test_cache_does_not_leak_between_different_cache_dicts():
    # тот же id строки, но разная цена и СВОЙ (новый) кеш для каждого
    # расчёта — вторая сумма не должна взять устаревшее значение из
    # первого расчёта
    line_v1 = make_line(id="x", qty=10.0, price=5.0)
    result_v1 = calculate_amount(line_v1, {"x": line_v1}, cache={})

    line_v2 = make_line(id="x", qty=10.0, price=999.0)
    result_v2 = calculate_amount(line_v2, {"x": line_v2}, cache={})

    assert result_v1 == pytest.approx(50.0)
    assert result_v2 == pytest.approx(9990.0)
    assert result_v1 != result_v2


# --- необязательное округление строки (EstimateLine.rounding) ---


def test_rounding_defaults_to_none_and_keeps_old_behaviour():
    line = make_line(qty=1.0, price=38.5)
    assert line.rounding is None
    assert calculate_amount(line, {}) == 38.5


@pytest.mark.parametrize(
    "value, digits, expected",
    [(38.5, 0, 39.0), (10.2, 0, 10.0), (142.8, 0, 143.0), (-38.5, 0, -39.0),
     (2.5, 0, 3.0), (1225808.0999, 2, 1225808.1), (0.125, 2, 0.13)],
)
def test_excel_round_half_away_from_zero(value, digits, expected):
    from core.models import excel_round

    assert excel_round(value, digits) == expected


def test_kind_a_rounding_applies_to_line_amount():
    line = make_line(qty=1.0, price=77.0, coefs={"k": 0.5}, rounding=0)
    assert calculate_amount(line, {}) == 39.0  # 38,5 → 39, как ROUND в Excel


def test_kind_b_sums_already_rounded_base_lines_then_rounds_itself():
    a = make_line(id="a", qty=1.0, price=77.0, coefs={"k": 0.5}, rounding=0)  # 38,5 → 39
    b = make_line(id="b", qty=1.0, price=34.0, coefs={"k": 0.3}, rounding=0)  # 10,2 → 10
    total = make_line(id="t", kind="B", base_ids=["a", "b"], coefs={"rate": 0.25}, rounding=0)
    lines = {line.id: line for line in (a, b, total)}
    # база 49 (не 48,7) × 0,25 = 12,25 → 12
    assert calculate_amount(total, lines) == 12.0


def test_rounding_survives_json_round_trip_and_old_json_without_key():
    from core.stages import lines_from_json, lines_to_json

    line = make_line(rounding=0)
    assert lines_from_json(lines_to_json({line.id: line}))[line.id].rounding == 0

    old = lines_to_json({line.id: make_line()})
    del old[0]["data"]["rounding"]  # снимок, сохранённый до появления поля
    assert lines_from_json(old)[line.id].rounding is None


# --- аудит 05.11.2026, О-6: округление как ROUND в Excel (значения — из настоящего Excel 16) ---


@pytest.mark.parametrize("value, expected", [
    (1.5 * 15 * 1.4, 32.0),   # в двоичном виде 31,499999999999996
    (2.5 * 20 * 1.15, 58.0),
    (2.8 * 3 * 1.25, 11.0),
    (38.5, 39.0),
    (-38.5, -39.0),
    (38.49, 38.0),
])
def test_excel_round_matches_excel_on_products(value, expected):
    assert excel_round(value, 0) == expected


@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_excel_round_does_not_fail_on_non_finite(value):
    assert excel_round(value, 0) == value
