"""Геодезия: черновик ввода ведомости по ключевым объёмам (core/tz.py, режим
«ключевые объёмы», 03.11.2026).

Ключевые объёмы (решения исполнителя): площадь основной топографической съёмки
(масштаб и сечение — из ТЗ, страница 1), протяжённость изысканий трассы, число
пунктов плановой опорной сети, число точек привязки геологических выработок (у
геодезии своё число — с числом скважин геологии в документах заказчика не
совпадает, список вопросов В-22). Достраивается по связям, проверенным на двух
эталонных объектах (docs/proverka_vedomosti.md, раздел 5):

- жёсткие (оба объекта + физический смысл): высотная опорная сеть, камеральная
  обработка плановой сети и знаки — по числу пунктов плановой сети (те же
  пункты); инженерно-топографический план = съёмка того же масштаба, сечения и
  площади (план чертят по съёмке);
- оценка по архиву: содержание базы экспедиции — 2 мес. (так на обоих объектах;
  на самом деле зависит от срока полевых работ).
"""

from __future__ import annotations

from core.module_base import Field
from core.tz import (
    KEY_NOTE,
    KIND_ARCHIVE,
    KIND_KEY,
    KIND_RULE,
    OBJECT_LINEAR,
    Proposal,
    TzParams,
    archive_note,
    key_value,
    link_note,
)

K_AREA = "survey_area_ha"
K_TRASSA = "trassa_km"
K_OPORNY = "oporny_points"
K_PRIVYAZKA = "privyazka_points"

# Содержание базы экспедиции, мес. — 2 на обоих эталонных объектах (листы ИГДИ,
# строка базы т.82). ДОПУЩЕНИЕ до ответа ОИИ: зависит от продолжительности работ.
BASE_MONTHS = 2


def key_volume_fields() -> list[Field]:
    return [
        Field(name=K_AREA, label="Площадь основной топографической съёмки, га (масштаб и "
              "сечение — из блока ТЗ)", type="number", required=False),
        Field(name=K_TRASSA, label="Изыскания трассы, км", type="number", required=False),
        Field(name=K_OPORNY, label="Пунктов плановой опорной сети", type="number",
              required=False, integer=True),
        Field(name=K_PRIVYAZKA, label="Точек привязки геологических выработок", type="number",
              required=False, integer=True),
    ]


def propose(module, tz: TzParams, keys: dict) -> Proposal:
    from modules.geodesy import module as geo

    proposal = module.propose_volumes(tz)
    area = key_value(keys, K_AREA)
    if area is not None:
        if tz.survey_scale and tz.survey_section_m:
            linear = tz.object_kind == OBJECT_LINEAR
            strip = linear and tz.survey_scale in geo.STRIP_SCALES
            condition = geo.CONDITION_STRIP if strip else geo.CONDITION_NORMAL
            row = {"topo_scale": tz.survey_scale, "topo_section": tz.survey_section_m,
                   "topo_area": area}
            note = f"{KEY_NOTE}; масштаб {tz.survey_scale} и сечение {tz.survey_section_m:g} м — из ТЗ"
            if strip:
                note += "; условие «полоса вдоль трассы» (к = 0,7) — оценка для линейного объекта"
            proposal.set(geo.VK_TOPO_SURVEYS, [{**row, "survey_condition": condition}], KIND_KEY, note)
            proposal.set(geo.VK_TOPO_PLANS, [{**row, "plan_condition": condition}], KIND_RULE,
                         link_note("план = съёмка того же масштаба, сечения и площади (оба объекта)"))
        else:
            proposal.notes.append(
                "Площадь съёмки задана ключевым объёмом, но в блоке ТЗ нет масштаба и сечения — "
                "строка съёмки не предложена."
            )
    trassa = key_value(keys, K_TRASSA)
    if trassa is not None:
        proposal.set(geo.VK_TRASSA, trassa, KIND_KEY, KEY_NOTE)
    oporny = key_value(keys, K_OPORNY, integer=True)
    if oporny is not None:
        proposal.set(geo.VK_OPORNY, oporny, KIND_KEY, KEY_NOTE)
        for name, what in ((geo.VK_HEIGHT, "высотная сеть"),
                           (geo.VK_OPORNY_KAMERAL, "камеральная обработка плановой сети"),
                           (geo.VK_ZNAKI, "знаки опорной сети")):
            proposal.set(name, oporny, KIND_RULE, link_note(
                f"{what} = пунктов плановой опорной сети (те же пункты; оба объекта)"))
    privyazka = key_value(keys, K_PRIVYAZKA, integer=True)
    if privyazka is not None:
        proposal.set(geo.VK_PRIVYAZKA, privyazka, KIND_KEY, KEY_NOTE)
    if any(v is not None for v in (area, trassa, oporny, privyazka)):  # есть полевые работы
        proposal.set(geo.VK_BASE, BASE_MONTHS, KIND_ARCHIVE, archive_note(
            f"содержание базы {BASE_MONTHS} мес. — так на обоих объектах; зависит от срока работ"))
    return proposal
