from core.module_base import Field
from core.validation import missing_required_fields

SCHEMA = [
    Field(name="drilling_length", label="Метраж бурения, п.м.", type="number", required=True),
    Field(name="soil_category", label="Категория грунта", type="text", required=True),
    Field(name="comment", label="Комментарий", type="text", required=False),
]


def test_all_required_fields_filled_returns_empty_list():
    data = {"drilling_length": 300, "soil_category": "IV", "comment": ""}
    assert missing_required_fields(SCHEMA, data) == []


def test_empty_required_text_field_is_reported():
    data = {"drilling_length": 300, "soil_category": "", "comment": ""}
    assert missing_required_fields(SCHEMA, data) == ["Категория грунта"]


def test_zero_required_number_field_is_a_value_not_missing():
    # С Э2 (25.10.2026): 0 у числового поля — значение («не измеряется»),
    # а не «не заполнено» (раньше 0 считался пустым).
    data = {"drilling_length": 0, "soil_category": "IV"}
    assert missing_required_fields(SCHEMA, data) == []


def test_zero_required_positive_number_field_is_reported():
    schema = [Field(name="n", label="Число скважин", type="number", required=True, positive=True)]
    assert missing_required_fields(schema, {"n": 0}) == ["Число скважин"]
    assert missing_required_fields(schema, {"n": 3}) == []


def test_none_required_number_field_is_reported():
    assert missing_required_fields(SCHEMA, {"drilling_length": None, "soil_category": "IV"}) == [
        "Метраж бурения, п.м."
    ]


def test_missing_key_entirely_is_reported():
    data = {"soil_category": "IV"}  # drilling_length вообще не передан
    assert missing_required_fields(SCHEMA, data) == ["Метраж бурения, п.м."]


def test_optional_field_missing_is_not_reported():
    data = {"drilling_length": 300, "soil_category": "IV"}  # comment вообще не передан
    assert missing_required_fields(SCHEMA, data) == []


def test_multiple_missing_required_fields_all_reported():
    data = {}
    assert missing_required_fields(SCHEMA, data) == [
        "Метраж бурения, п.м.",
        "Категория грунта",
    ]


# --- type="group" (повторяющаяся группа строк) ---

GROUP_SCHEMA = [
    Field(
        name="drilling_by_category",
        label="Бурение по категориям грунта",
        type="group",
        required=True,
        group_fields=[
            Field(name="soil_category", label="Категория грунта", type="text", required=True),
            Field(name="drilling_length", label="Метраж бурения, п.м.", type="number", required=True),
        ],
    ),
]


def test_group_field_with_rows_is_not_reported_as_missing():
    data = {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 300}]}
    assert missing_required_fields(GROUP_SCHEMA, data) == []


def test_group_field_with_empty_list_is_reported_as_missing():
    data = {"drilling_by_category": []}
    assert missing_required_fields(GROUP_SCHEMA, data) == ["Бурение по категориям грунта"]


def test_group_field_missing_key_entirely_is_reported():
    data = {}
    assert missing_required_fields(GROUP_SCHEMA, data) == ["Бурение по категориям грунта"]
