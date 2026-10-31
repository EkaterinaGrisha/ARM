"""Words of the synthetic kits: organization codes, names, materials, purchased items, drawing field annotations.

Each annotation carries the parameters the extraction of Block 2 should find in it, as :class:`Param` written the way
the demo kit truth writes them. Technical requirements come from templates (:mod:`synth.requirements`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple


class Param(NamedTuple):
    """A parameter the extraction of Block 2 should find: its category and name, as the demo kit truth writes them,
    and the value or the range with its unit when the template that wrote it knows them."""

    category: str
    name: str
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None


Params = tuple[Param, ...]

LITERA_SCALE = ("П", "Э", "Т", "О", "О1", "О2", "О3", "А", "Б")  # GOST 2.103, from the earliest stage
FORMAT_NAMES = {"A3": "А3", "A4": "А4"}  # the formats the renderer draws, as a specification writes them


def lower_litera(litera: str) -> str:
    """The stage before ``litera``."""
    return LITERA_SCALE[LITERA_SCALE.index(litera) - 1]


# Letters of an organization code by GOST 2.201: Ё, З, Й, О, Ч, Ъ, Ы and Ь are not used.
ORG_CODES = ("АБВГ", "КТРМ", "ПМЛС", "ВЕДС", "ДРТК", "ГСНП", "ТЕХМ", "ЛМКР", "СПРН", "МАШН")

# Classification characteristics by GOST 2.201 for the classifier scheme of designations, by item.
ASSEMBLY_CLASSES = ("301411", "302231", "303411", "304125", "305312", "381146")
PART_CLASSES = {
    "shaft": ("715211", "715311", "715422"),
    "gear": ("721321", "721411", "722155"),
    "disc": ("711141", "711321", "713111"),
    "housing": ("731311", "731112", "732211"),
    "bracket": ("741124", "741321", "745131"),
    "nut": ("758411", "758422"),
}

ASSEMBLY_NAMES = (
    "Редуктор",
    "Привод",
    "Механизм подъёма",
    "Опора",
    "Корпус в сборе",
    "Вал ведущий в сборе",
    "Вал ведомый в сборе",
    "Ступица в сборе",
    "Колесо в сборе",
    "Тормоз",
    "Муфта",
    "Кронштейн в сборе",
    "Крышка в сборе",
    "Блок шестерён",
    "Рама",
    "Кожух в сборе",
    "Насос",
    "Натяжитель",
)

# What decides the heat treatment and the coating of a part: a case-hardening steel is carburized, a medium carbon or
# alloy steel is improved or hardened, a low carbon steel and the castings are not heat treated; steels are plated or
# oxidized, the castings and the plate painted, aluminium anodized, bronze left as machined.
Grade = Literal["case", "steel", "mild", "iron", "aluminium", "bronze"]


@dataclass(frozen=True)
class Material:
    text: str  # as the title block writes it
    grade: Grade


STEEL_45 = Material("Сталь 45 ГОСТ 1050-2013", "steel")
STEEL_40X = Material("Сталь 40Х ГОСТ 4543-2016", "steel")


@dataclass(frozen=True)
class Annotation:
    text: str
    params: Params = ()


@dataclass(frozen=True)
class PartKind:
    """A kind of part: its names, how its view is drawn, its materials and what the field of its drawing says.

    ``surfaces`` name what an item of the technical requirements is about, «Шейки А и Б»; ``variable`` is the dimension
    that changes from one variant of a group drawing to another, with the range of its values in mm.
    """

    names: tuple[str, ...]
    sketch: str
    materials: tuple[Material, ...]
    annotations: tuple[Annotation, ...]
    general_roughness: tuple[str, ...] = ("Ra 6,3", "Ra 12,5")
    formats: tuple[str, ...] = ("A3", "A4")
    surfaces: tuple[str, ...] = ("Поверхность А",)
    variable: tuple[str, int, int] = ("L", 40, 200)


PART_KINDS = (
    PartKind(
        ("Вал", "Вал ведущий", "Вал промежуточный", "Ось", "Палец", "Тяга"),
        "shaft",
        (STEEL_45, STEEL_40X),
        (
            Annotation("Ø40k6", (Param("dimension_tolerance", "Ø40k6"),)),
            Annotation("Ø35h7", (Param("dimension_tolerance", "Ø35h7"),)),
            Annotation("Ra 0,8", (Param("roughness", "Ra 0,8"),)),
            Annotation("Ra 1,6", (Param("roughness", "Ra 1,6"),)),
            Annotation("↗ 0,02 А", (Param("geometric", "↗"),)),
            Annotation("315"),
        ),
        surfaces=("Шейки А и Б", "Поверхности Б", "Шейку под подшипник"),
        variable=("L", 180, 420),
    ),
    PartKind(
        ("Шестерня", "Шестерня ведущая", "Колесо зубчатое", "Колесо червячное", "Звёздочка"),
        "gear",
        (Material("Сталь 20ХН3А ГОСТ 4543-2016", "case"), Material("Сталь 18ХГТ ГОСТ 4543-2016", "case"), STEEL_40X),
        (
            Annotation("Ø40h6", (Param("dimension_tolerance", "Ø40h6"),)),
            Annotation("Ø62,5"),
            Annotation("Ra 0,8", (Param("roughness", "Ra 0,8"),)),
            Annotation("⊥ 0,02 А", (Param("geometric", "⊥"),)),
            Annotation("48"),
        ),
        surfaces=("Зубья", "Зубчатый венец"),
        variable=("B", 20, 60),
    ),
    PartKind(
        ("Втулка", "Крышка", "Крышка торцевая", "Фланец", "Кольцо распорное", "Стакан", "Шайба специальная"),
        "disc",
        (Material("Ст3сп ГОСТ 380-2005", "mild"), Material("Бронза БрО5Ц5С5 ГОСТ 613-79", "bronze"), STEEL_45),
        (
            Annotation("Ø105h8", (Param("dimension_tolerance", "Ø105h8"),)),
            Annotation("Ø62H7", (Param("dimension_tolerance", "Ø62H7"),)),
            Annotation("Ø80"),
            Annotation("Ra 3,2", (Param("roughness", "Ra 3,2"),)),
        ),
        general_roughness=("Ra 12,5",),
        formats=("A4",),
        surfaces=("Поверхность А", "Торец Б"),
        variable=("B", 8, 60),
    ),
    PartKind(
        ("Корпус", "Корпус подшипника", "Крышка корпуса", "Основание"),
        "housing",
        (Material("СЧ 20 ГОСТ 1412-85", "iron"), Material("АК7ч ГОСТ 1583-93", "aluminium")),
        (
            Annotation("Ø110H7", (Param("dimension_tolerance", "Ø110H7"),)),
            Annotation("Ø90H8", (Param("dimension_tolerance", "Ø90H8"),)),
            Annotation("Ø240"),
            Annotation("Ra 1,6", (Param("roughness", "Ra 1,6"),)),
            Annotation("∥ 0,05 Б", (Param("geometric", "∥"),)),
        ),
        general_roughness=("Ra 12,5",),
        formats=("A3",),
        surfaces=("Поверхность А", "Отверстия Б"),
        variable=("H", 120, 260),
    ),
    PartKind(
        ("Кронштейн", "Планка", "Пластина", "Стойка", "Рычаг", "Упор"),
        "bracket",
        (Material("Сталь 3 ГОСТ 380-2005", "mild"), Material("Сталь 20 ГОСТ 1050-2013", "mild")),
        (
            Annotation("120"),
            Annotation("Ø18H12", (Param("dimension_tolerance", "Ø18H12"),)),
            Annotation("Ra 6,3", (Param("roughness", "Ra 6,3"),)),
        ),
        general_roughness=("Ra 12,5", "Ra 25"),
        surfaces=("Поверхность А",),
        variable=("L", 80, 240),
    ),
    PartKind(
        ("Гайка специальная", "Гайка регулировочная", "Винт специальный"),
        "nut",
        (STEEL_45,),
        (
            Annotation("М48×1,5-6H", (Param("dimension_tolerance", "М48×1,5-6H"),)),
            Annotation("Ø70"),
            Annotation("Ra 3,2", (Param("roughness", "Ra 3,2"),)),
        ),
        general_roughness=("Ra 12,5",),
        formats=("A4",),
        surfaces=("Резьбу",),
        variable=("H", 12, 40),
    ),
)

STANDARD_ITEMS = (
    "Болт М8×25 ГОСТ 7798-70",
    "Болт М12×40 ГОСТ 7798-70",
    "Гайка М8 ГОСТ 5915-70",
    "Гайка М12 ГОСТ 5915-70",
    "Шайба 8 65Г ГОСТ 6402-70",
    "Шайба 12 65Г ГОСТ 6402-70",
    "Винт М6×16 ГОСТ 17473-80",
    "Подшипник 205 ГОСТ 8338-75",
    "Подшипник 7310 ГОСТ 27365-87",
    "Шпонка 2-8×7×32 ГОСТ 23360-78",
    "Кольцо 040-045-30 ГОСТ 9833-73",
    "Манжета 1.1-40×60-1 ГОСТ 8752-79",
    "Штифт 6×30 ГОСТ 3128-70",
    "Манжета армированная 1.1-40×60-1 ГОСТ 8752-79",
    "Кольцо уплотнительное резиновое 040-045-30-2-2 ГОСТ 9833-73",
)
OTHER_ITEMS = (
    "Электродвигатель асинхронный АИР80А4 IM1081 220/380 В ТУ 16-525.564-84",
    "Датчик давления ДД-10 ТУ 4212-001-2023",
)
MATERIALS = (("Масло ТМ-5-18 ГОСТ 17479.2-2015", "л"), ("Смазка Литол-24 ГОСТ 21150-2017", "кг"))

# Ink stamps of a paper copy: the lines of each, «{n}» an inventory number and «{date}» a date. Round ones are short.
STAMPS = (
    ("КОПИЯ ВЕРНА",),
    ("КОНТРОЛЬНЫЙ", "ЭКЗЕМПЛЯР"),
    ("ДУБЛИКАТ",),
    ("УЧТЁННЫЙ", "ЭКЗ. № {n}"),
    ("АРХИВ", "ИНВ. № {n}"),
    ("ИЗМЕНЕНИЕ ВНЕСЕНО", "{date}"),
    ("ПРОВЕРЕНО", "Н. КОНТРОЛЬ"),
)
ROUND_STAMPS = (("ОТК", "{n}"), ("БТК", "{n}"), ("ОГК",))
