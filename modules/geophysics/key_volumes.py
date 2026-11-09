"""Геофизика: черновик ввода ведомости по ключевым объёмам (core/tz.py, режим
«ключевые объёмы», 03.11.2026).

Ключевые объёмы (решения исполнителя): число физических точек ВЭЗ
(вертикального электрического зондирования), объём сейсморазведки МПВ (метода
преломлённых волн) на один тип волн и тип волн (выпадающий список). Камеральную
обработку МПВ модуль считает сам (= полевой объём своего типа волн,
`build_volume_sheet`). Лист геофизики есть только у одного эталонного объекта,
поэтому жёстких связей (по правилу «оба объекта + физический смысл») здесь нет —
всё достроенное помечено оценкой:

- технический отчёт — один на раздел при полевых работах (так у всех разделов
  обоих объектов, но лист геофизики — у одного);
- камеральная обработка электроразведки — при ВЭЗ (один объект).
"""

from __future__ import annotations

from core.module_base import Field
from core.tz import KEY_NOTE, KIND_ARCHIVE, KIND_KEY, Proposal, TzParams, archive_note, key_text, key_value

K_VEZ = "vez_points"
K_MPV = "mpv_observations"
K_WAVES = "mpv_waves"
# Тип волн в ключевых объёмах: непустые коды (пусто — «не задано»); «P» в форме
# модуля — значение по умолчанию DEFAULT_CHOICE (продольные).
WAVES_KEY_OPTIONS = [
    ("P", "продольные волны"),
    ("S", "поперечные волны"),
    ("PS", "оба типа — продольные и поперечные"),
]


def key_volume_fields() -> list[Field]:
    return [
        Field(name=K_VEZ, label="ВЭЗ (электрозондирование), физ. точек", type="number",
              required=False, integer=True),
        Field(name=K_MPV, label="Сейсморазведка МПВ, физ. наблюдений на один тип волн",
              type="number", required=False, integer=True),
        Field(name=K_WAVES, label="МПВ: тип волн", type="text", required=False,
              dynamic_options=True),
    ]


def key_volume_options(name: str) -> list[tuple[str, str]]:
    return list(WAVES_KEY_OPTIONS) if name == K_WAVES else []


def propose(module, tz: TzParams, keys: dict) -> Proposal:
    from modules.geophysics import module as gph

    proposal = module.propose_volumes(tz)
    vez = key_value(keys, K_VEZ, integer=True)
    mpv = key_value(keys, K_MPV, integer=True)
    waves = key_text(keys, K_WAVES)
    form_waves = {"P": gph.DEFAULT_CHOICE, "S": gph.WAVES_S, "PS": gph.WAVES_BOTH}.get(waves)
    if vez is not None:
        proposal.set("vez_count", vez, KIND_KEY, KEY_NOTE)
        proposal.set(gph.F_OFFICE_VEZ, gph.YES, KIND_ARCHIVE, archive_note(
            "камеральная обработка электроразведки — при ВЭЗ (лист есть у одного объекта)"))
    if mpv is not None:
        proposal.set("mpv_count", mpv, KIND_KEY, KEY_NOTE)
        if form_waves is not None:
            proposal.set(gph.F_MPV_WAVES, form_waves, KIND_KEY, KEY_NOTE)
    elif form_waves is not None:
        proposal.notes.append(
            "Внимание: тип волн МПВ задан, а объём сейсморазведки МПВ — нет; тип волн не "
            "применён. Задайте объём МПВ в ключевых объёмах или на странице ведомости."
        )
    if vez is not None or mpv is not None:
        proposal.set(gph.F_REPORT, gph.YES, KIND_ARCHIVE, archive_note(
            "технический отчёт — один на раздел при полевых работах (так у всех разделов обоих "
            "объектов, но лист геофизики — только у одного)"))
    return proposal
