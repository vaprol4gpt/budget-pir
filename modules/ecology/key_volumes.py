"""Экология: черновик ввода ведомости по ключевым объёмам (core/tz.py, режим
«ключевые объёмы», 03.11.2026).

Ключевые объёмы (решения исполнителя): протяжённость рекогносцировки
(маршрутов), число точек наблюдений, площадь радиационного обследования, число
проб поверхностной воды, число проб почв на естественные радионуклиды. Почвенную
рекогносцировку, наблюдения по маршруту и все камеральные пары модуль считает сам
(= полевой объём, `build_volume_sheet`). Здесь — по связям, проверенным на двух
эталонных объектах (docs/proverka_vedomosti.md, раздел 5):

- жёсткие (оба объекта + физический смысл — каждая проба получает свой анализ):
  химический состав поверхностной воды = пробам поверхностной воды; тяжёлые
  металлы, бенз(а)пирен, нефтепродукты в донных отложениях = пробам донных
  отложений; гамма-спектрометрия = пробам почв на радионуклиды;
- оценки по архиву: проб донных отложений = проб поверхностной воды (так на обоих
  объектах — отбор в тех же створах, но на мелком водотоке донных проб может не
  быть); тяжёлые металлы, бенз(а)пирен, нефтепродукты в почвах = пробам почв на
  радионуклиды (совпало на обоих объектах, физической связи нет).
"""

from __future__ import annotations

from core.module_base import Field
from core.tz import KEY_NOTE, KIND_ARCHIVE, KIND_KEY, Proposal, TzParams, archive_note, key_value

K_RECON = "recon_km"
K_POINTS = "observation_points"
K_RADIATION = "radiation_ha"
K_SURFACE_WATER = "surface_water_samples"
K_RADIONUCLIDES = "radionuclide_samples"


def key_volume_fields() -> list[Field]:
    return [
        Field(name=K_RECON, label="Инженерно-экологическая рекогносцировка (маршруты), км",
              type="number", required=False),
        Field(name=K_POINTS, label="Точек наблюдений (описание на эколого-карте)", type="number",
              required=False, integer=True),
        Field(name=K_RADIATION, label="Площадь радиационного обследования, га", type="number",
              required=False),
        Field(name=K_SURFACE_WATER, label="Проб поверхностной воды (химия)", type="number",
              required=False, integer=True),
        Field(name=K_RADIONUCLIDES, label="Проб почв на естественные радионуклиды", type="number",
              required=False, integer=True),
    ]


def propose(module, tz: TzParams, keys: dict) -> Proposal:
    from modules.ecology import module as eco

    proposal = module.propose_volumes(tz)
    recon = key_value(keys, K_RECON)
    if recon is not None:
        proposal.set(eco.VK_RECON_LENGTH, recon, KIND_KEY,
                     f"{KEY_NOTE}; почвенная рекогносцировка и маршрут модуль ставит равными ей")
    points = key_value(keys, K_POINTS, integer=True)
    if points is not None:
        proposal.set(eco.VK_OBSERVATION_POINTS, points, KIND_KEY, KEY_NOTE)
    radiation = key_value(keys, K_RADIATION)
    if radiation is not None:
        proposal.set(eco.VK_RADIATION, radiation, KIND_KEY, KEY_NOTE)

    water = key_value(keys, K_SURFACE_WATER, integer=True)
    nuclides = key_value(keys, K_RADIONUCLIDES, integer=True)
    samples, analyses, notes = [], [], []
    if water is not None:
        samples += [{"sample_type": "surface_water_chemical", "sample_count": water},
                    {"sample_type": "bottom_sediments_chemical", "sample_count": water}]
        analyses += [{"analysis_type": "surface_water", "analysis_count": water}]
        analyses += [{"analysis_type": kind, "analysis_count": water}
                     for kind in ("heavy_metals_sediments", "pah_sediments", "oil_sediments")]
        notes.append("поверхностная вода — ключевой объём; донные отложения = пробам воды "
                     "(оценка по архиву: оба объекта, на мелком водотоке донных проб может не быть)")
    if nuclides is not None:
        samples.append({"sample_type": "soil_radionuclides", "sample_count": nuclides})
        analyses.append({"analysis_type": "gamma_spectrometry", "analysis_count": nuclides})
        analyses += [{"analysis_type": kind, "analysis_count": nuclides}
                     for kind in ("heavy_metals_soil", "pah_soil", "oil_soil")]
        notes.append("пробы на радионуклиды — ключевой объём; тяжёлые металлы, бенз(а)пирен и "
                     "нефтепродукты в почвах = им (оценка по архиву: совпало на обоих объектах, "
                     "физической связи нет)")
    if samples:
        proposal.set(eco.VK_SAMPLING, samples, KIND_ARCHIVE, archive_note("; ".join(notes)))
        proposal.set(eco.VK_LAB_ANALYSES, analyses, KIND_ARCHIVE, archive_note(
            "жёсткая связь «анализ = проба» (каждая проба — свой анализ, оба объекта) от проб "
            "выше; " + "; ".join(notes)))
        proposal.notes.append(
            "Остальные виды проб (почвы на химию, агрохимия, санитарные, подземные воды, "
            "биотестирование) и их анализы — ввод оператора; гранулометрия = пробам почв и "
            "грунтов на химию (так на обоих объектах) — добавьте вместе с этими пробами."
        )
    return proposal
