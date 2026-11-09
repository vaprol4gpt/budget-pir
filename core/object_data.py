"""Общие данные объекта — «шаг 0» расчёта (Э5 дорожной карты, 25.10.2026).

Один набор на объект, вводится до выбора модуля и общий для всех модулей:
шифр, наименование, заказчик, регион, расстояние до объекта и маршрут,
даты, сезон, признак неблагоприятного периода, примечание. Хранится вместе
с версией сметы и черновиком (`core/persistence.py`), идёт в выгрузку.

Модулям данные объекта доступны через `refs`: интерфейс и скрипты передают
в расчёт не `ReferenceBook`, а `CalculationContext` — тот же справочник
(все его методы) плюс атрибут `object_data`. Модуль читает
`object_data_of(refs)` и работает и со старым `refs` без данных объекта
(тесты, прямой вызов) — тогда данные объекта считаются не заданными.

Первое применение — расстояние до объекта выбирает пункт внутреннего
транспорта СБЦ-99 т.4 (`transport_item_by_distance`). Флаг
неблагоприятного периода применяют геология (1,2, начисление уровня сметы) и
гидрометеорология (× 1,4 к полевым строкам, с 30.10.2026; с 05.11.2026 — только
при выборе «по отметке объекта» на этапе её сметы, по умолчанию — «не применять»).
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from core.module_base import Field
from core.tz import TzParams


@dataclass(frozen=True)
class ObjectData:
    code: str = ""                    # шифр объекта
    name: str = ""                    # наименование объекта
    customer: str = ""                # заказчик
    region: str = ""                  # регион
    distance_km: float | None = None  # расстояние от базы до объекта, км; None — не задано
    route: str = ""                   # маршрут
    start_date: str | None = None     # дата начала работ, ISO
    end_date: str | None = None       # дата окончания работ, ISO
    season: str = ""                  # сезон работ
    unfavorable_period: bool = False  # работы в неблагоприятный период
    note: str = ""                    # примечание
    # Ставки начислений уровня сметы (core/accruals.py, С3 30.10.2026), доля
    # базы: 0,5 = +50 %. None — не задано, начисления нет; 0 — не начислять.
    regional_field_rate: float | None = None     # районный + северные к полевым
    regional_office_rate: float | None = None    # районный + северные к камеральным
    external_transport_rate: float | None = None  # внешний транспорт
    org_liquidation_rate: float | None = None    # организация и ликвидация работ
    org_liquidation_k: float | None = None       # коэффициент к ней (Крайний Север 2,5)
    # Ставки по разделу, перекрывающие общие (находка С8: в эталонах на одном
    # объекте ставки разные по разделам): {модуль: {поле ставки: значение}}.
    accrual_overrides: dict = dataclasses.field(default_factory=dict)
    # Расстояние для внутреннего транспорта по разделу, км (боевой прогон
    # 31.10.2026: в сметах компании разделы одного объекта берут разные пункты
    # СБЦ-99 т.4 — разные участки работ): {модуль: км}. Нет ключа — общее
    # расстояние объекта `distance_km`. Только разделы TRANSPORT_MODULES.
    transport_distance_overrides: dict = dataclasses.field(default_factory=dict)
    # Коэффициенты снижения цены (Э9, страница «5. Сводный бюджет»): {"budget":
    # к сводной, <модуль>: к разделу после индекса}; нет ключа — 1 (без
    # снижения). Решение о цене всего объекта: живёт в сессии браузера и идёт в
    # выгрузку Excel; в базу смет попадает только вместе с данными объекта при
    # следующем подтверждении этапа / черновике любого раздела. Открытие
    # версии раздела его НЕ перезаписывает (ревью сводной №1, app/ui.py).
    price_reductions: dict = dataclasses.field(default_factory=dict)
    # Показывать «в т.ч. мобилизация/демобилизация» в сводном бюджете (итог
    # не меняется; правило — только в сметах заказчика 2026 г., В-27). Хранится
    # так же, как price_reductions.
    show_mobilization: bool = False
    # Параметры технического задания и решения исполнителя (core/tz.py, 03.11.2026):
    # словарь TzParams.to_dict() — только заданные значения; пусто — не заданы.
    # Хранится в том же JSON данных объекта (формат базы не меняется); старые
    # версии без ключа открываются с пустыми параметрами. На расчёт сметы не влияет.
    tz: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "ObjectData":
        """Из словаря (сохранённого JSON или ввода формы). Неизвестные ключи
        пропускаются (данные новее программы не роняют открытие),
        отсутствующие — значение по умолчанию."""
        known = {f.name for f in dataclasses.fields(cls)}
        values = {k: v for k, v in (data or {}).items() if k in known}
        # Нечисловые значения в таблицах (битые сохранённые данные, ревью
        # сводной №3) пропускаются с предупреждением, а не роняют открытие версии.
        skipped: list[str] = []

        def number(value, label):
            parsed = _parse_number(value)
            if parsed is None and value not in (None, ""):
                skipped.append(f"{label}: «{value}»")
            return parsed

        overrides = values.get("accrual_overrides")
        if overrides is not None:
            # только известные разделы и поля ставок, только заданные значения
            values["accrual_overrides"] = {
                module: cleaned
                for module, rates in _as_dict(overrides).items() if module in ACCRUAL_MODULES
                if (cleaned := {
                    k: parsed for k, v in _as_dict(rates).items() if k in ACCRUAL_RATE_LABELS
                    if (parsed := number(v, f"{ACCRUAL_MODULES[module]}: "
                                            f"{ACCRUAL_RATE_LABELS[k]}")) is not None
                })
            }
        distances = values.get("transport_distance_overrides")
        if distances is not None:
            # только известные разделы; пусто и 0 — «не задано» (как distance_km)
            values["transport_distance_overrides"] = {
                module: parsed for module, v in _as_dict(distances).items()
                if module in TRANSPORT_MODULES
                if (parsed := number(v, f"{TRANSPORT_MODULES[module]}: расстояние для "
                                        "внутреннего транспорта")) not in (None, 0.0)
            }
        reductions = values.get("price_reductions")
        if reductions is not None:
            # только заданные коэффициенты в (0; 1]; 1 — то же, что не задано
            values["price_reductions"] = {
                key: parsed for key, v in _as_dict(reductions).items() if key in REDUCTION_LABELS
                if (parsed := number(v, f"Коэффициент снижения: {REDUCTION_LABELS[key]}"))
                is not None and 0 < parsed < 1
            }
        if "tz" in values:
            # битое / неизвестное внутри параметров ТЗ пропускается (core/tz.py)
            values["tz"] = TzParams.from_dict(values["tz"]).to_dict()
        # Числовые поля объекта (расстояние, ставки начислений): строка с запятой
        # («12,5») понимается, нечисловое/бесконечное — пропускается с
        # предупреждением, а не роняет открытие версии (итоговое ревью, №2, №3).
        for name, field_def in cls.__dataclass_fields__.items():
            if field_def.type in ("float | None",) and name in values:
                raw = values[name]
                if raw in (None, ""):
                    values[name] = None
                    continue
                label = ACCRUAL_RATE_LABELS.get(name) or ("расстояние до объекта, км" if name == "distance_km" else name)
                values[name] = number(raw, label)
        if values.get("distance_km") in ("", 0, 0.0):
            # 0 в числовом поле формы = «не задано» (расстояние до объекта
            # не бывает нулевым: работы всегда на выезде от базы).
            values["distance_km"] = None
        result = cls(**values)
        if skipped:
            # не поле данных (не сохраняется и не участвует в сравнении)
            object.__setattr__(result, "_load_warnings", [
                f"В сохранённых данных объекта нечисловое значение — пропущено: {item}."
                for item in skipped
            ])
        return result

    def load_warnings(self) -> list[str]:
        """Что пропущено при чтении сохранённых данных (`from_dict`)."""
        return list(getattr(self, "_load_warnings", []))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def rate(self, module: str, name: str) -> float | None:
        """Ставка начисления для раздела: своя ставка раздела, если задана,
        иначе общая ставка объекта."""
        own = self.accrual_overrides.get(module, {}).get(name)
        return own if own is not None else getattr(self, name)

    def transport_distance(self, module: str | None) -> tuple[float | None, str]:
        """Расстояние для внутреннего транспорта раздела и откуда оно взято:
        своё расстояние раздела, если задано, иначе общее расстояние объекта."""
        own = self.transport_distance_overrides.get(module) if module else None
        if own is not None:
            return own, f"расстояние раздела «{TRANSPORT_MODULES[module]}»"
        return self.distance_km, "общее расстояние объекта"

    def reduction(self, key: str) -> float:
        """Коэффициент снижения: `"budget"` — сводной, код модуля — раздела;
        не задан — 1."""
        return self.price_reductions.get(key, 1.0)

    def is_empty(self) -> bool:
        return self == ObjectData()

    def warnings(self) -> list[str]:
        """Предупреждения по самим данным объекта."""
        result = self.load_warnings()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            result.append("Дата окончания работ раньше даты начала — проверьте данные объекта.")
        if self.distance_km is not None and self.distance_km < 0:
            result.append("Расстояние до объекта отрицательное — проверьте данные объекта.")
        for module, value in self.transport_distance_overrides.items():
            if value < 0:
                result.append(
                    f"{TRANSPORT_MODULES[module]}: расстояние для внутреннего транспорта "
                    "отрицательное — проверьте данные объекта."
                )
        if self.unfavorable_period:
            result.append(
                "Отмечены работы в неблагоприятный период: коэффициент применяется у геологии "
                "(СБЦ-99 т.2 п.4 — 1,2 к полевым работам, как в эталоне); у гидрометеорологии "
                "(× 1,4 к каждой полевой строке, как в шаблоне компании) — только если на этапе её "
                "сметы выбрано «по отметке объекта» (по умолчанию — «не применять»); у геофизики, "
                "геодезии и экологии в эталонах его нет — не применяется."
            )
        checks = [(label, getattr(self, name)) for name, label in ACCRUAL_RATE_LABELS.items()]
        checks += [
            (f"{ACCRUAL_MODULES[module]}: {ACCRUAL_RATE_LABELS[name]}", value)
            for module, rates in self.accrual_overrides.items() for name, value in rates.items()
        ]
        for label, value in checks:
            if value is not None and not 0 <= value <= 3:
                result.append(
                    f"{label}: {value:g} — ожидается доля (например, 0,5 = 50 %), проверьте ввод."
                )
        return result

    def info(self) -> dict[str, str]:
        """Заполненные поля — для листа «Сведения» выгрузки."""
        rows = {}
        for field in OBJECT_FIELDS:
            value = getattr(self, field.name)
            if value in (None, "", False):
                continue
            if field.name in ("start_date", "end_date"):
                value = date.fromisoformat(value).strftime("%d.%m.%Y")
            elif isinstance(value, bool):
                value = "да"
            elif field.name == "price_reductions":
                value = "; ".join(f"{REDUCTION_LABELS[k]} ×{v:g}" for k, v in value.items())
                if not value:
                    continue
            elif field.name == "tz":
                value = "; ".join(f"{k}: {v}" for k, v in TzParams.from_dict(value).describe().items())
                if not value:
                    continue
            elif field.name == "transport_distance_overrides":
                value = "; ".join(f"{TRANSPORT_MODULES[m]}: {v:g} км" for m, v in value.items())
                if not value:
                    continue
            elif isinstance(value, dict):
                value = "; ".join(
                    f"{ACCRUAL_MODULES[m]}: " + ", ".join(
                        f"{ACCRUAL_RATE_LABELS[k]} {v:g}" for k, v in rates.items()
                    )
                    for m, rates in value.items()
                )
                if not value:
                    continue
            rows[field.label] = str(value)
        return rows


# Поля формы «Данные объекта» (подписи — для интерфейса и выгрузки). Все
# необязательные: расчёт сметы данных объекта не требует.
BASE_OBJECT_FIELDS = [
    Field(name="code", label="Шифр объекта", type="text", required=False),
    Field(name="name", label="Наименование объекта", type="text", required=False),
    Field(name="customer", label="Заказчик", type="text", required=False),
    Field(name="region", label="Регион", type="text", required=False),
    Field(name="distance_km", label="Расстояние от базы до объекта, км", type="number",
          required=False),
    Field(name="route", label="Маршрут", type="text", required=False),
    Field(name="start_date", label="Дата начала работ", type="date", required=False),
    Field(name="end_date", label="Дата окончания работ", type="date", required=False),
    Field(name="season", label="Сезон работ", type="text", required=False),
    Field(name="unfavorable_period", label="Работы в неблагоприятный период", type="bool",
          required=False),
    Field(name="note", label="Примечание", type="text", required=False),
]

# Ставки начислений (С3) — отдельный блок формы «Начисления». Подсказки —
# значения эталонных смет компании «Автодорога»/«Карьер» (ДОПУЩЕНИЕ: в эталонах на
# одном объекте они разные по разделам; норма — список вопросов В-18), таблица —
# docs/nachisleniya.md.
ACCRUAL_RATE_LABELS = {
    "regional_field_rate": "Районный коэффициент и северные надбавки к полевым работам, доля",
    "regional_office_rate": "Районный коэффициент и северные надбавки к камеральным работам, доля",
    "external_transport_rate": "Внешний транспорт, доля",
    "org_liquidation_rate": "Организация и ликвидация работ, доля",
    "org_liquidation_k": "Коэффициент к организации и ликвидации (районы Крайнего Севера)",
}
ACCRUAL_RATE_HELP = {
    "regional_field_rate": "Надбавка к полевым работам: 0,5 = +50 %. В эталонах компании (Магадан): "
    "геология 0,5; геофизика 0,66; геодезия 0,3; экология 0,6; ИГМИ — свой ввод (1,85). "
    "Пусто — не начисляется.",
    "regional_office_rate": "Надбавка к камеральным работам: в эталонах (регион работ) 0,25; у "
    "геофизики не начисляется. Пусто — не начисляется.",
    "external_transport_rate": "В эталонах: 0,392 (СБЦ-99/СБЦи т.5, свыше 2000 км); геофизика "
    "0,065 (СЦиР-1982). Пусто — не начисляется; 0 — не начислять.",
    "org_liquidation_rate": "В эталонах 0,06 (ОУ п.13). Пусто — не начисляется.",
    "org_liquidation_k": "В эталонах 2,5 (ОУ п.13 прим.1, Крайний Север) у геологии и "
    "геодезии; у экологии без коэффициента. Пусто — 1.",
}
ACCRUAL_FIELDS = [
    Field(name=name, label=label, type="number", required=False)
    for name, label in ACCRUAL_RATE_LABELS.items()
]
# Разделы с начислениями по core/accruals.py (у ИГМИ — свои, этап сметы).
ACCRUAL_MODULES = {"geology": "Геология", "geophysics": "Геофизика", "geodesy": "Геодезия", "ecology": "Экология"}
# Значения эталонных листов компании по разделу — подсказка у поля ставки раздела
# (docs/nachisleniya.md; ДОПУЩЕНИЕ, В-18/В-25).
ACCRUAL_ETALON_HINTS = {
    "geology": {"regional_field_rate": 0.5, "regional_office_rate": 0.25,
                "external_transport_rate": 0.392, "org_liquidation_rate": 0.06,
                "org_liquidation_k": 2.5},
    "geophysics": {"regional_field_rate": 0.66, "external_transport_rate": 0.065},
    "geodesy": {"regional_field_rate": 0.3, "regional_office_rate": 0.25,
                "external_transport_rate": 0.392, "org_liquidation_rate": 0.06,
                "org_liquidation_k": 2.5},
    "ecology": {"regional_field_rate": 0.6, "regional_office_rate": 0.25,
                "external_transport_rate": 0.392, "org_liquidation_rate": 0.06,
                "org_liquidation_k": 1.0},
}
# Все поля данных объекта (порядок = порядок полей ObjectData).
OBJECT_FIELDS = BASE_OBJECT_FIELDS + ACCRUAL_FIELDS + [
    Field(name="accrual_overrides", label="Ставки начислений по разделам",
          type="accrual_table", required=False),
    Field(name="transport_distance_overrides",
          label="Расстояние для внутреннего транспорта по разделам",
          type="distance_table", required=False),
    # вводятся на странице «5. Сводный бюджет», не в блоке данных объекта
    Field(name="price_reductions", label="Коэффициенты снижения цены",
          type="reduction_table", required=False),
    Field(name="show_mobilization", label="Показывать мобилизацию/демобилизацию",
          type="bool", required=False),
    # вводятся в блоке «Параметры технического задания» (core/tz.py)
    Field(name="tz", label="Параметры ТЗ", type="tz_table", required=False),
]
# Разделы, у которых внутренний транспорт СБЦ-99 т.4 выбирается по расстоянию
# (transport_item_by_distance) — у них может быть своё расстояние.
TRANSPORT_MODULES = {"geology": "Геология", "ecology": "Экология"}
# Где применяется коэффициент снижения (ключ ObjectData.price_reductions).
REDUCTION_LABELS = {
    "budget": "Сводный бюджет", **ACCRUAL_MODULES, "hydromet": "Гидрометеорология",
}


def _as_dict(value: Any) -> dict:
    """Таблица из сохранённых данных: словарь или пусто (битое — пусто)."""
    return value if isinstance(value, dict) else {}


def _parse_number(value: Any) -> float | None:
    """Число из сохранённого значения: число, строка с точкой или запятой
    («0,8» = 0,8); пусто, не число, NaN или бесконечность — None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip().replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    return parsed if math.isfinite(parsed) else None


class CalculationContext:
    """Справочник + данные объекта: передаётся модулям вместо `refs`.
    Все атрибуты, кроме `object_data`, — от справочника."""

    def __init__(self, refs: Any, object_data: ObjectData | None = None):
        self._refs = refs
        self.object_data = object_data or ObjectData()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._refs, name)


def object_data_of(refs: Any) -> ObjectData:
    """Данные объекта из `refs` (CalculationContext) или пустые."""
    data = getattr(refs, "object_data", None)
    return data if isinstance(data, ObjectData) else ObjectData()


_RANGE = re.compile(r"от\s+(\d+(?:[.,]\d+)?)\s+до\s+(\d+(?:[.,]\d+)?)\s*км", re.IGNORECASE)


def distance_ranges(refs: Any, source: str, table: str) -> list[tuple[float, float, str]]:
    """Диапазоны расстояний «от A до B км» из НАИМЕНОВАНИЙ позиций справочника
    (source, table) — [(A, B, пункт)], по возрастанию A. Позиции без
    диапазона в наименовании пропускаются (диапазон не выдумываем).
    Справочник без `list_items` (двойники в тестах) — пустой список."""
    if not hasattr(refs, "list_items"):
        return []
    ranges = []
    for item, category, _price, name in refs.list_items(source, table):
        if category is not None:
            continue
        match = _RANGE.search(name or "")
        if match:
            low, high = (float(g.replace(",", ".")) for g in match.groups())
            ranges.append((low, high, item))
    return sorted(ranges)


@dataclass(frozen=True)
class TransportChoice:
    item: str
    # Пояснение к основанию строки транспорта; пусто — пункт по умолчанию
    # модуля без данных объекта (прежнее поведение).
    note: str
    warning: str | None


def transport_item_by_distance(
    refs: Any, source: str, table: str, default_item: str, module: str | None = None
) -> TransportChoice:
    """Пункт внутреннего транспорта по расстоянию (данные объекта в `refs`) и
    диапазонам из справочника. Расстояние — своё расстояние раздела `module`,
    если задано, иначе общее расстояние объекта (`ObjectData.transport_distance`).
    Расстояние не задано или не попадает ни в один найденный диапазон —
    `default_item` (пункт эталона модуля) с предупреждением."""
    distance, origin = object_data_of(refs).transport_distance(module)
    if distance is None:
        return TransportChoice(
            default_item, "",
            f"Внутренний транспорт: расстояние до объекта не задано (данные объекта) — "
            f"принят пункт эталона т.{table} п.{default_item}.",
        )
    ranges = distance_ranges(refs, source, table)
    for low, high, item in ranges:
        if low <= distance <= high:
            return TransportChoice(
                item, f"по расстоянию {distance:g} км ({origin}; от {low:g} до {high:g} км)", None
            )
    known = ", ".join(f"п.{item}: {low:g}–{high:g} км" for low, high, item in ranges) or "нет"
    return TransportChoice(
        default_item, f"расстояние {distance:g} км ({origin}) — диапазон не найден, пункт эталона",
        f"Внутренний транспорт: для расстояния {distance:g} км ({origin}) пункт т.{table} не определяется "
        f"по справочнику (найденные диапазоны: {known}) — принят пункт эталона "
        f"п.{default_item}, проверьте вручную.",
    )
