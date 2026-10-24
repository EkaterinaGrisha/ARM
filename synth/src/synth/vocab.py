"""Words of the synthetic kits: organization codes, names, materials, purchased items, technical requirements.

Each requirement and annotation carries the parameters the extraction of Block 2 should find in it, as
``(category, name)`` pairs written the way the demo kit truth writes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Params = tuple[tuple[str, str], ...]

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


@dataclass(frozen=True)
class Annotation:
    text: str
    params: Params = ()


@dataclass(frozen=True)
class PartKind:
    """A kind of part: its names, how its view is drawn, its materials and what the field of its drawing says."""

    names: tuple[str, ...]
    sketch: str
    materials: tuple[str, ...]
    annotations: tuple[Annotation, ...]
    general_roughness: tuple[str, ...] = ("Ra 6,3", "Ra 12,5")
    formats: tuple[str, ...] = ("A3", "A4")
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Requirement:
    text: str
    params: Params = ()


GENERAL_TOLERANCE = Requirement(
    "Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2.", (("general_tolerance", "IT14"),)
)
CARBURIZING = Requirement(
    "Цементировать h 0,8...1,2 мм, 58...62 HRC.", (("heat_treatment", "Цементация"), ("hardness", "HRC"))
)
HARDENING = Requirement(
    "Закалить ТВЧ h 1,5...2 мм, 48...52 HRC.", (("heat_treatment", "Закалка ТВЧ"), ("hardness", "HRC"))
)
IMPROVEMENT = Requirement("235...262 HB.", (("hardness", "HB"),))
HARD_NUT = Requirement("30...35 HRC.", (("hardness", "HRC"),))
ZINC = Requirement("Покрытие Ц9.хр.", (("coating", "Ц9.хр"),))
OXIDE = Requirement("Покрытие Хим.Окс.прм.", (("coating", "Хим.Окс.прм"),))
PAINT = Requirement(
    "Покрытие наружных необработанных поверхностей: Грунт ГФ-021, Эмаль ПФ-115 серая.", (("coating", "Грунт ГФ-021"),)
)
EDGES = Requirement("Острые кромки притупить R0,5.")
CAST_RADII = Requirement("Неуказанные литейные радиусы R3...5 мм.")
REFERENCE = Requirement("Размеры для справок.")

PART_KINDS = (
    PartKind(
        ("Вал", "Вал ведущий", "Вал промежуточный", "Ось", "Палец", "Тяга"),
        "shaft",
        ("Сталь 45 ГОСТ 1050-2013", "Сталь 40Х ГОСТ 4543-2016"),
        (
            Annotation("Ø40k6", (("dimension_tolerance", "Ø40k6"),)),
            Annotation("Ø35h7", (("dimension_tolerance", "Ø35h7"),)),
            Annotation("Ra 0,8", (("roughness", "Ra 0,8"),)),
            Annotation("Ra 1,6", (("roughness", "Ra 1,6"),)),
            Annotation("↗ 0,02 А", (("geometric", "↗"),)),
            Annotation("315"),
        ),
        requirements=(HARDENING, IMPROVEMENT, EDGES),
    ),
    PartKind(
        ("Шестерня", "Шестерня ведущая", "Колесо зубчатое", "Колесо червячное", "Звёздочка"),
        "gear",
        ("Сталь 20ХН3А ГОСТ 4543-2016", "Сталь 40Х ГОСТ 4543-2016"),
        (
            Annotation("Ø40h6", (("dimension_tolerance", "Ø40h6"),)),
            Annotation("Ø62,5"),
            Annotation("Ra 0,8", (("roughness", "Ra 0,8"),)),
            Annotation("⊥ 0,02 А", (("geometric", "⊥"),)),
            Annotation("48"),
        ),
        requirements=(CARBURIZING, EDGES),
    ),
    PartKind(
        ("Втулка", "Крышка", "Крышка торцевая", "Фланец", "Кольцо распорное", "Стакан", "Шайба специальная"),
        "disc",
        ("Ст3сп ГОСТ 380-2005", "Бронза БрО5Ц5С5 ГОСТ 613-79", "Сталь 45 ГОСТ 1050-2013"),
        (
            Annotation("Ø105h8", (("dimension_tolerance", "Ø105h8"),)),
            Annotation("Ø62H7", (("dimension_tolerance", "Ø62H7"),)),
            Annotation("Ø80"),
            Annotation("Ra 3,2", (("roughness", "Ra 3,2"),)),
        ),
        general_roughness=("Ra 12,5",),
        formats=("A4",),
        requirements=(OXIDE, ZINC),
    ),
    PartKind(
        ("Корпус", "Корпус подшипника", "Крышка корпуса", "Основание"),
        "housing",
        ("СЧ 20 ГОСТ 1412-85", "АК7ч ГОСТ 1583-93"),
        (
            Annotation("Ø110H7", (("dimension_tolerance", "Ø110H7"),)),
            Annotation("Ø90H8", (("dimension_tolerance", "Ø90H8"),)),
            Annotation("Ø240"),
            Annotation("Ra 1,6", (("roughness", "Ra 1,6"),)),
            Annotation("∥ 0,05 Б", (("geometric", "∥"),)),
        ),
        general_roughness=("Ra 12,5",),
        formats=("A3",),
        requirements=(CAST_RADII, PAINT),
    ),
    PartKind(
        ("Кронштейн", "Планка", "Пластина", "Стойка", "Рычаг", "Упор"),
        "bracket",
        ("Сталь 3 ГОСТ 380-2005", "Сталь 20 ГОСТ 1050-2013"),
        (
            Annotation("120"),
            Annotation("Ø18H12", (("dimension_tolerance", "Ø18H12"),)),
            Annotation("Ra 6,3", (("roughness", "Ra 6,3"),)),
        ),
        general_roughness=("Ra 12,5", "Ra 25"),
        requirements=(PAINT, EDGES),
    ),
    PartKind(
        ("Гайка специальная", "Гайка регулировочная", "Винт специальный"),
        "nut",
        ("Сталь 45 ГОСТ 1050-2013",),
        (
            Annotation("М48×1,5-6H", (("dimension_tolerance", "М48×1,5-6H"),)),
            Annotation("Ø70"),
            Annotation("Ra 3,2", (("roughness", "Ra 3,2"),)),
        ),
        general_roughness=("Ra 12,5",),
        formats=("A4",),
        requirements=(HARD_NUT, ZINC),
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
