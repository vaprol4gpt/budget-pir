from core.module_base import Field


def test_field_dynamic_options_defaults_to_false():
    field = Field(name="drilling_length", label="Метраж", type="number", required=True)
    assert field.dynamic_options is False


def test_field_integer_defaults_to_false():
    field = Field(name="drilling_length", label="Метраж", type="number", required=True)
    assert field.integer is False


def test_field_integer_can_be_set_explicitly():
    field = Field(name="drilling_count", label="Число скважин", type="number", required=True, integer=True)
    assert field.integer is True


def test_field_dynamic_options_can_be_set_explicitly():
    field = Field(
        name="soil_category",
        label="Категория грунта",
        type="text",
        required=True,
        dynamic_options=True,
    )
    assert field.dynamic_options is True


def test_field_group_fields_defaults_to_none():
    field = Field(name="drilling_by_category", label="Бурение по категориям", type="group", required=True)
    assert field.group_fields is None


def test_field_group_fields_can_hold_nested_fields():
    sub_fields = [
        Field(name="soil_category", label="Категория грунта", type="text", required=True),
        Field(name="drilling_length", label="Метраж бурения, п.м.", type="number", required=True),
    ]
    field = Field(
        name="drilling_by_category",
        label="Бурение по категориям",
        type="group",
        required=True,
        group_fields=sub_fields,
    )
    assert field.group_fields == sub_fields
