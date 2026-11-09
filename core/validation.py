"""Проверка полей ввода — общая для всех модулей (не специфичная для
геологии), поэтому живёт в core/, а не в modules/geology/.
`Field.required` (core/module_base.py) был объявлен, но нигде не
проверялся — этот модуль восполняет проверку, не меняя сам контракт."""

from __future__ import annotations

from typing import Any, Mapping

from core.module_base import Field


def _is_missing(field: Field, value: Any) -> bool:
    if value is None:
        return True
    if field.type == "group":
        return not value
    if field.type == "number":
        # 0 — значение, а не «пусто» (Э2 дорожной карты, 25.10.2026):
        # «радон не измеряется» = 0 точек. Для полей, где 0 смысла не
        # имеет, модуль ставит Field.positive=True.
        return field.positive and not value
    return value == ""


def missing_required_fields(schema: list[Field], data: dict) -> list[str]:
    """Вернуть подписи (label) обязательных полей, которые не заполнены.

    «Не заполнено»: значения нет в `data` (или None), пустая строка у
    текстового поля, пустой список строк у type="group". Для
    type="number" 0 — допустимое значение, если поле не помечено
    `Field.positive=True` (тогда 0 тоже «не заполнено»).

    Содержимое отдельных строк type="group" (например, строка с
    выбранной категорией, но нулевым метражом) этой функцией намеренно
    не проверяется — только наличие хотя бы одной строки.
    """
    return [
        field.label
        for field in schema
        if field.required and _is_missing(field, data.get(field.name))
    ]


def _below(field: Field, value: Any) -> bool:
    if field.type != "number" or field.min_value is None or value is None:
        return False
    try:
        return float(value) < field.min_value
    except (TypeError, ValueError):
        return False


def below_min_fields(schema: list[Field], data: Mapping[str, Any]) -> list[str]:
    """Подписи числовых полей (и под-полей строк групп), значение которых
    меньше `Field.min_value` — по умолчанию отрицательные. Под-поле группы
    подписывается «<группа>, строка N: <поле>»."""
    result = []
    for field in schema:
        value = data.get(field.name)
        if field.type == "group":
            for number, row in enumerate(value or [], start=1):
                for sub_field in field.group_fields or []:
                    if _below(sub_field, row.get(sub_field.name)):
                        result.append(f"{field.label}, строка {number}: {sub_field.label}")
        elif _below(field, value):
            result.append(field.label)
    return result


def _is_negative(value: Any) -> bool:
    return isinstance(value, (int, float)) and value < 0


def negative_quantity_warnings(lines: Mapping[str, Any]) -> list[str]:
    """Предупреждения по строкам результата этапа с отрицательным объёмом, ценой или
    ставкой доли от базы — например, после правки оператора или из старых данных
    (опечатка со знаком уменьшает итог; аудит 05.11.2026, О-9)."""
    warnings = []
    for line in lines.values():
        for what, value in (("объём", getattr(line, "qty", None)),
                            ("цена", getattr(line, "price", None)),
                            ("ставка", (getattr(line, "coefs", None) or {}).get("rate"))):
            if _is_negative(value):
                warnings.append(f"Отрицательн{'ый' if what == 'объём' else 'ая'} {what} в строке "
                                f"«{line.name}»: {value:g} — проверьте")
    return warnings


# Откуда объём строки ведомости задан человеком (остальные — связи и авто-строки:
# программа работ и техотчёт равны 1 и на пустой форме).
_ENTERED_SOURCES = ("input", "operator")


def empty_volume_sheet(lines: Mapping[str, Any]) -> bool:
    """В ведомости не задано ни одного объёма: все строки ввода и правки — нули. Такую
    ведомость подтверждать нельзя (аудит 05.11.2026, О-7, В-62): получается версия сметы
    из одних авто-строк, а звёздочка «обязательное» у числового поля 0 пропускает."""
    return not any(
        getattr(line, "source", None) in _ENTERED_SOURCES and (getattr(line, "qty", 0) or 0) > 0
        for line in lines.values()
    )
