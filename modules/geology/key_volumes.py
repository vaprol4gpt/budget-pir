"""Геология: черновик ввода ведомости по ключевым объёмам (core/tz.py, режим
«ключевые объёмы», 03.11.2026).

Инженер задаёт четыре главных объёма (решения исполнителя): число скважин,
метраж бурения, протяжённость рекогносцировки, метраж с гидронаблюдениями.
Остальное:

- модуль, как и прежде, считает сам из числа скважин и метража: привязка,
  камеральная обработка бурения (= метраж без гидронаблюдений) и с
  гидронаблюдениями, камеральная рекогносцировки, программа, отчёт (класс А),
  термометрия (Б), монолиты и пробы воды (В, середина диапазона) —
  `build_volume_sheet`;
- здесь — поля ввода по связям, проверенным на двух эталонных объектах
  (линейном и площадном, docs/proverka_vedomosti.md, раздел 5):
  * жёсткие (оба объекта + физический смысл): крепление скважин = метраж с
    гидронаблюдениями (скважина под наблюдения за водой закрепляется);
    коррозионная активность к бетону = к стали (одни и те же образцы, два
    определения);
  * оценки по архиву: физические свойства глинистых + песчаных грунтов = числу
    монолитов дисперсных грунтов (сумма — на обоих объектах, разбиение 75–80 % /
    20–25 % — свойство грунтов участка, взята середина 77,5 %); коррозия к стали =
    числу проб воды (совпало на обоих объектах, но физической связи нет —
    docs/predmetnaya_oblast.md).
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

K_HOLES = "holes_count"
K_DRILLING = "drilling_total_m"
K_RECON = "rekognoscirovka_km"
K_GIDRO = "gidronablyudeniya_m"

# Доля глинистых в «глинистые + песчаные» — 0,80 и 0,75 на двух эталонных
# объектах (листы ИГИ, раздел VII); середина. ДОПУЩЕНИЕ до правила ОИГ.
CLAY_SHARE = (0.80 + 0.75) / 2


def key_volume_fields() -> list[Field]:
    return [
        Field(name=K_HOLES, label="Число скважин", type="number", required=False, integer=True),
        Field(name=K_DRILLING, label="Метраж бурения всего, п.м.", type="number", required=False),
        Field(name=K_RECON, label="Инженерно-геологическая рекогносцировка, км", type="number",
              required=False),
        Field(name=K_GIDRO, label="Метраж скважин с гидрогеологическими наблюдениями, п.м.",
              type="number", required=False),
    ]


def propose(module, tz: TzParams, keys: dict) -> Proposal:
    from modules.geology import module as geo  # константы профиля — без цикла импорта

    proposal = module.propose_volumes(tz)
    holes = key_value(keys, K_HOLES, integer=True)
    if holes is not None:
        proposal.set("drilling_count", holes, KIND_KEY, KEY_NOTE)
    drilling = key_value(keys, K_DRILLING)
    if drilling is not None:
        proposal.set(
            "drilling_by_category", [{"soil_category": None, "drilling_length": drilling}], KIND_KEY,
            f"{KEY_NOTE}; категории грунта — ввод: разнесите метраж по фактическим категориям "
            "буримости",
        )
    recon = key_value(keys, K_RECON)
    if recon is not None:
        proposal.set("rekognoscirovka_km", recon, KIND_KEY, KEY_NOTE)
    gidro = key_value(keys, K_GIDRO)
    if gidro is not None:
        proposal.set("gidronablyudeniya_length", gidro, KIND_KEY, KEY_NOTE)
        proposal.set("kreplenie_length", gidro, KIND_RULE, link_note(
            "крепление скважин = метраж с гидронаблюдениями (так на обоих эталонных объектах)"))
    holes = holes if holes is not None else proposal.inputs.get("drilling_count")
    if holes:
        monolity = geo.MONOLITY_DISPERSNYE_PER_HOLE * holes
        clay = round(monolity * CLAY_SHARE)
        proposal.set("lab_glinistye_count", clay, KIND_ARCHIVE, archive_note(
            f"глинистые + песчаные = монолитам дисперсных ({monolity:g} — типовой профиль "
            f"{geo.MONOLITY_DISPERSNYE_PER_HOLE:g} на скважину); глинистых {CLAY_SHARE:.1%} — "
            "середина 75–80 % двух объектов"))
        proposal.set("lab_peschanye_count", round(monolity) - clay, KIND_ARCHIVE, archive_note(
            "остаток монолитов дисперсных после глинистых"))
        water = round(geo.PROBY_VODY_PER_HOLE * holes)
        if water:
            proposal.set("lab_korroziya_stal_count", water, KIND_ARCHIVE, archive_note(
                f"= числу проб воды ({geo.PROBY_VODY_PER_HOLE:g} на скважину): так на обоих "
                "объектах, но физической связи нет"))
            # связь «к бетону = к стали» жёсткая, но само число — оценка: производное
            # от оценки остаётся оценкой (ревью трека 1, №2)
            proposal.set("lab_korroziya_beton_count", water, KIND_ARCHIVE, archive_note(
                "к бетону = к стали (жёсткая связь: одни образцы, два определения), но число "
                "проб к стали — оценка"))
    proposal.notes.append(
        "Режим «ключевые объёмы»: не выводятся испытания штампом, база экспедиции, сбор "
        "материалов прошлых лет, оптимальная влажность — ввод оператора."
    )
    return proposal
