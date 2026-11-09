"""Гидрометеорология: черновик ввода ведомости по ключевым объёмам (core/tz.py,
режим «ключевые объёмы», 03.11.2026).

Ключевые объёмы (решения исполнителя, по карте): число обследуемых водотоков
(гидролого-морфометрическое обследование), число водосборов для расчётов стока,
число промерных створов, число измерений расхода воды, протяжённость
рекогносцировки трассы. Камеральные пары модуль считает сам (= полевой объём,
`build_volume_sheet`). Здесь — по связям, проверенным на двух эталонных объектах
(docs/proverka_vedomosti.md, раздел 5):

- жёсткие (оба объекта + физический смысл): промеры глубин = промерным створам
  (промер на каждом створе); графики зависимости и кривая расходов = измерениям
  расхода (по графику и кривой на каждый створ измерения); определение уклона
  водосбора = площади водосбора = числу водосборов; расчёты максимального,
  весеннего и минимального расхода — «первый водосбор» 1 и «последующие» =
  водосборов − 1 (так устроены сами пункты таблицы);
- оценки по архиву (оба объекта, но это практика, а не физика): рекогносцировка
  реки и морфостворы — 1 км на водоток; отметки высоких вод (ГВВ) = измерениям
  расхода; фотоработы — 10 на водоток; измерений расхода, если не заданы, —
  2 на водоток.
"""

from __future__ import annotations

from core.module_base import Field
from core.tz import (
    KEY_NOTE,
    KIND_ARCHIVE,
    KIND_KEY,
    KIND_RULE,
    Proposal,
    TzParams,
    archive_note,
    key_value,
    link_note,
)

K_WATERCOURSES = "watercourses"
K_BASINS = "basins"
K_STVORS = "sounding_stvors"
K_FLOW = "flow_measurements"
K_RECON = "recon_route_km"

# Практика двух эталонных листов ИГМИ (на водоток): км рекогносцировки реки и
# морфостворов, фоторабот, измерений расхода. ДОПУЩЕНИЕ до ответа ОИИ.
KM_PER_WATERCOURSE = 1
PHOTO_PER_WATERCOURSE = 10
FLOW_PER_WATERCOURSE = 2


def key_volume_fields() -> list[Field]:
    return [
        Field(name=K_WATERCOURSES, label="Обследуемых водотоков", type="number", required=False,
              integer=True),
        Field(name=K_BASINS, label="Водосборов (для расчётов стока)", type="number",
              required=False, integer=True),
        Field(name=K_STVORS, label="Промерных створов", type="number", required=False,
              integer=True),
        Field(name=K_FLOW, label="Измерений расхода воды", type="number", required=False,
              integer=True),
        Field(name=K_RECON, label="Рекогносцировка трассы, км", type="number", required=False),
    ]


def propose(module, tz: TzParams, keys: dict) -> Proposal:
    from modules.hydromet import module as h

    proposal = module.propose_volumes(tz)
    recon = key_value(keys, K_RECON)
    if recon is not None:
        proposal.set(h.VK_RECON_ROUTE, recon, KIND_KEY, KEY_NOTE)
    rivers = key_value(keys, K_WATERCOURSES, integer=True)
    if rivers is not None:
        proposal.set(h.VK_MORPHOMETRIC, rivers, KIND_KEY, KEY_NOTE)
        for name, what in ((h.VK_RECON_RIVER, "рекогносцировка реки"),
                           (h.VK_MORPHO_STVOR, "разбивка и нивелирование морфостворов")):
            proposal.set(name, rivers * KM_PER_WATERCOURSE, KIND_ARCHIVE, archive_note(
                f"{what} — {KM_PER_WATERCOURSE} км на водоток (так на обоих объектах)"))
        proposal.set(h.VK_PHOTO, rivers * PHOTO_PER_WATERCOURSE, KIND_ARCHIVE, archive_note(
            f"фотоработы — {PHOTO_PER_WATERCOURSE} на водоток (так на обоих объектах)"))
    flow = key_value(keys, K_FLOW, integer=True)
    flow_is_key = flow is not None
    if flow is not None:
        proposal.set(h.VK_FLOW, flow, KIND_KEY, KEY_NOTE)
    elif rivers is not None:
        flow = rivers * FLOW_PER_WATERCOURSE
        proposal.set(h.VK_FLOW, flow, KIND_ARCHIVE, archive_note(
            f"измерения расхода — {FLOW_PER_WATERCOURSE} на водоток (так на обоих объектах)"))
    if flow:
        proposal.set(h.VK_GVV, flow, KIND_ARCHIVE, archive_note(
            "отметки высоких вод (ГВВ) = измерениям расхода (так на обоих объектах)"))
        link = "= измерениям расхода (график и кривая на каждый створ измерения; оба объекта)"
        for name in (h.VK_FLOW_GRAPHS, h.VK_RATING_CURVE):
            if flow_is_key:
                proposal.set(name, flow, KIND_RULE, link_note(link))
            else:  # производное от оценки — оценка (ревью трека 1, №2)
                proposal.set(name, flow, KIND_ARCHIVE, archive_note(
                    f"{link}, но число измерений само оценено ({FLOW_PER_WATERCOURSE} на водоток)"))
    stvors = key_value(keys, K_STVORS, integer=True)
    if stvors is not None:
        proposal.set(h.VK_SOUNDING_STVOR, stvors, KIND_KEY, KEY_NOTE)
        proposal.set(h.VK_DEPTH, stvors, KIND_RULE, link_note(
            "промеры глубин = промерным створам (промер на каждом створе; оба объекта)"))
    basins = key_value(keys, K_BASINS, integer=True)
    if basins is not None:
        proposal.set(h.VK_BASIN_AREA, basins, KIND_KEY, KEY_NOTE)
        proposal.set(h.VK_BASIN_SLOPE, basins, KIND_RULE, link_note(
            "уклон водосбора — по каждому водосбору (= площади водосбора; оба объекта)"))
        for first, nxt, what in ((h.VK_MAX_FLOW_FIRST, h.VK_MAX_FLOW_NEXT, "максимального"),
                                 (h.VK_SPRING_FIRST, h.VK_SPRING_NEXT, "весеннего половодья"),
                                 (h.VK_MIN_FLOW_FIRST, h.VK_MIN_FLOW_NEXT, "минимального")):
            proposal.set(first, 1, KIND_RULE, link_note(
                f"расчёт {what} расхода — первый водосбор: 1"))
            if basins > 1:
                proposal.set(nxt, basins - 1, KIND_RULE, link_note(
                    f"расчёт {what} расхода — последующие водосборы: водосборов − 1"))
    proposal.notes.append(
        "Режим «ключевые объёмы»: нивелирование уклонов, вспомогательные таблицы, параметры "
        "стока, аналог, смещение и деформации русла — ввод оператора (на двух объектах "
        "устойчивой связи нет)."
    )
    return proposal
