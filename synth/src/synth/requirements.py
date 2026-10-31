"""Technical requirements of drawings from templates of typical items, with the parameters each item holds.

The items of a part drawing follow the order of GOST 2.316-2008, 4.12: the material, its heat treatment and hardness;
dimensions, their limit deviations, tolerances of form and position; the quality of surfaces and coatings; then the
rest, such as marking. Which items a drawing gets depends on its part: a case-hardening steel is carburized, a cast
iron is painted, a bronze is neither (:class:`synth.vocab.Material`). Each template draws its values from ranges and
words its item one of several ways, as drawings of different bureaus and years do, and gives the parameters the
extraction of Block 2 should find in it, with their values where the item writes them: the depth of a hardened layer,
the hardness, the roughness, the tolerance.

Wordings: unspecified limit deviations as GOST 25670 wrote them and as GOST 2.307-2011, table 1 writes them; general
tolerances of form and position as GOST 2.308-2011, 4.7 writes them; roughness of a surface by GOST 2.309-73, 2.13;
coatings by GOST 9.306.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from synth.vocab import Material, Param, Params, PartKind


@dataclass(frozen=True)
class Requirement:
    text: str
    params: Params = ()


def _f(text: str) -> float:
    return float(text.replace(",", "."))


def _treatment(name: str, depth: tuple[str, str] | None = None) -> Param:
    """A heat treatment, with the depth of its layer when the item gives one."""
    if depth is None:
        return Param("heat_treatment", name)
    return Param("heat_treatment", name, value_min=_f(depth[0]), value_max=_f(depth[1]), unit="мм")


def _hardness(unit: str, low: int, high: int) -> Param:
    return Param("hardness", unit, value_min=float(low), value_max=float(high), unit=unit)


# ---------------------------------------------------------------------------------------- 1. heat treatment

CASE_DEPTH = (("0,8", "1,2"), ("1,0", "1,4"), ("1,2", "1,6"))
CASE_HRC = ((56, 62), (58, 62), (58, 63))
CORE_HRC = ((30, 40), (32, 42), (35, 45))
NITRO_DEPTH = (("0,3", "0,5"), ("0,4", "0,6"))
INDUCTION_DEPTH = (("1,5", "2"), ("1,5", "2,0"), ("2", "3"), ("2,0", "3,0"))
INDUCTION_HRC = ((45, 50), (48, 52), (48, 53), (50, 55))
HB = ((229, 269), (235, 262), (241, 285), (269, 302))
HRC = ((40, 45), (42, 48), (45, 50))
NUT_HRC = ((28, 32), (30, 35), (35, 40))


def _carburized(rng: random.Random, kind: PartKind) -> list[Requirement]:
    depth, (a, b) = rng.choice(CASE_DEPTH), rng.choice(CASE_HRC)
    layer = f"h {depth[0]}...{depth[1]} мм"
    match rng.randrange(4):
        case 0:
            return [
                Requirement(
                    f"Цементировать {layer}, {a}...{b} HRC.", (_treatment("Цементация", depth), _hardness("HRC", a, b))
                )
            ]
        case 1:
            c, d = rng.choice(CORE_HRC)
            text = f"{rng.choice(kind.surfaces)} цементировать {layer}, {a}...{b} HRCэ; сердцевина {c}...{d} HRCэ."
            return [
                Requirement(text, (_treatment("Цементация", depth), _hardness("HRCэ", a, b), _hardness("HRCэ", c, d)))
            ]
        case 2:
            text = f"Цементировать {layer}. Закалить, отпустить: {a}...{b} HRCэ."
            return [Requirement(text, (_treatment("Цементация", depth), _hardness("HRCэ", a, b)))]
        case _:
            depth = rng.choice(NITRO_DEPTH)
            text = f"Нитроцементировать h {depth[0]}...{depth[1]} мм, {a}...{b} HRC."
            return [Requirement(text, (_treatment("Нитроцементация", depth), _hardness("HRC", a, b)))]


def _improved(rng: random.Random) -> Requirement:
    a, b = rng.choice(HB)
    match rng.randrange(3):
        case 0:
            return Requirement(f"Улучшить до {a}...{b} HB.", (_treatment("Улучшение"), _hardness("HB", a, b)))
        case 1:
            return Requirement(f"{a}...{b} HB.", (_hardness("HB", a, b),))
        case _:
            return Requirement(
                f"Термообработка — улучшение, {a}...{b} HB.", (_treatment("Улучшение"), _hardness("HB", a, b))
            )


def _induction(rng: random.Random, kind: PartKind) -> Requirement:
    depth, (a, b) = rng.choice(INDUCTION_DEPTH), rng.choice(INDUCTION_HRC)
    verb = "закалить" if rng.random() < 0.6 else "Закалить"
    subject = f"{rng.choice(kind.surfaces)} " if verb == "закалить" else ""
    text = f"{subject}{verb} ТВЧ h {depth[0]}...{depth[1]} мм, {a}...{b} HRC."
    return Requirement(text, (_treatment("Закалка ТВЧ", depth), _hardness("HRC", a, b)))


def _hardened(rng: random.Random) -> Requirement:
    a, b = rng.choice(HRC)
    if rng.random() < 0.6:
        return Requirement(f"Закалить, отпустить: {a}...{b} HRC.", (_treatment("Закалка"), _hardness("HRC", a, b)))
    return Requirement(f"HRC {a}...{b}.", (_hardness("HRC", a, b),))


def _steel(rng: random.Random, kind: PartKind) -> list[Requirement]:
    """A medium carbon or alloy steel: a shaft or a gear is improved, hardened by induction or both; a nut is hardened
    through; a disc is improved, normalized or left as is."""
    if kind.sketch == "nut":
        a, b = rng.choice(NUT_HRC)
        if rng.random() < 0.5:
            return [Requirement(f"{a}...{b} HRC.", (_hardness("HRC", a, b),))]
        return [Requirement(f"Закалить до {a}...{b} HRC.", (_treatment("Закалка"), _hardness("HRC", a, b)))]
    if kind.sketch in ("shaft", "gear"):
        roll = rng.random()
        if roll < 0.35:
            return [_improved(rng), _induction(rng, kind)]
        if roll < 0.65:
            return [_induction(rng, kind)]
        return [_improved(rng)] if roll < 0.85 else [_hardened(rng)]
    roll = rng.random()
    if roll < 0.4:
        return [_improved(rng)]
    return [Requirement("Нормализовать.", (_treatment("Нормализация"),))] if roll < 0.55 else []


def heat_treatment(rng: random.Random, kind: PartKind, material: Material) -> list[Requirement]:
    if material.grade == "case":
        return _carburized(rng, kind)
    if material.grade == "steel":
        return _steel(rng, kind)
    return []


# ---------------------------------------------------------------------------------------- 2. dimensions

# Holes, shafts, the rest: the grade, and the class of GOST 30893.1 with the same deviations of holes and shafts.
GRADES = ((14, "t2"), (14, "t2"), (12, "t1"))


def general_tolerance(rng: random.Random) -> Requirement:
    """Limit deviations of the dimensions with no tolerance of their own: as GOST 25670 wrote them, or the general
    tolerances of GOST 30893.1 as GOST 2.307-2011, table 1 writes them."""
    grade, cls = rng.choice(GRADES)
    h = f"H{grade}, h{grade}"
    text = rng.choice(
        (
            f"Неуказанные предельные отклонения размеров: {h}, ±IT{grade}/2.",
            f"Неуказанные предельные отклонения размеров: отверстий H{grade}, валов h{grade}, остальных ±IT{grade}/2.",
            f"Общие допуски по ГОСТ 30893.1: {h}, ±IT{grade}/2.",
            f"Общие допуски по ГОСТ 30893.1: {h}, ±{cls}/2.",
        )
    )
    return Requirement(text, (Param("general_tolerance", f"IT{grade}"),))


GENERAL_FORM = Requirement(
    "Общие допуски формы и расположения — по ГОСТ 30893.2 — К.", (Param("geometric", "ГОСТ 30893.2 — К"),)
)
FORM = (  # the tolerance as the dictionary of the core names it, how an item words it, the tolerance in mm
    (
        "Допуск соосности",
        "Допуск соосности поверхностей {a} и {b} относительно общей оси Ø{v} мм.",
        ("0,02", "0,03", "0,05"),
    ),
    ("Допуск плоскостности", "Допуск плоскостности поверхности {a} — {v} мм.", ("0,03", "0,05", "0,1")),
    (
        "Допуск перпендикулярности",
        "Допуск перпендикулярности торца {a} относительно оси {b} — {v} мм.",
        ("0,02", "0,03", "0,05"),
    ),
    ("Допуск параллельности", "Допуск параллельности поверхностей {a} и {b} — {v} мм.", ("0,03", "0,05", "0,08")),
    (
        "Допуск радиального биения",
        "Радиальное биение поверхности {a} относительно оси {b} не более {v} мм.",
        ("0,02", "0,03", "0,04"),
    ),
)


def form_tolerance(rng: random.Random) -> Requirement:
    name, wording, values = rng.choice(FORM)
    value = rng.choice(values)
    text = wording.format(a=rng.choice("БВГ"), b="А", v=value)
    return Requirement(text, (Param("geometric", name, value=_f(value), unit="мм"),))


EDGES = Requirement("Острые кромки притупить R0,5.")
BLUNT = Requirement("Острые кромки притупить.")
CAST_RADII = Requirement("Неуказанные литейные радиусы R3...5 мм.")
DRAFTS = Requirement("Формовочные уклоны по ГОСТ 3212-92.")
CENTRES = Requirement("Центровые отверстия B4 ГОСТ 14034-74.")


def dimensions(rng: random.Random, kind: PartKind, material: Material) -> list[Requirement]:
    items = [general_tolerance(rng)] if rng.random() < 0.92 else []
    if kind.sketch in ("shaft", "gear", "disc", "housing") and rng.random() < 0.25:
        items.append(form_tolerance(rng))
    if rng.random() < 0.15:
        items.append(GENERAL_FORM)
    if material.grade in ("iron", "aluminium"):
        items += [CAST_RADII] if rng.random() < 0.8 else []
        items += [DRAFTS] if rng.random() < 0.3 else []
    elif rng.random() < 0.4:
        items.append(EDGES if rng.random() < 0.75 else BLUNT)
    if kind.sketch == "shaft" and rng.random() < 0.3:
        items.append(CENTRES)
    return items


# ---------------------------------------------------------------------------------------- 3. surfaces

RA = ("0,4", "0,8", "1,6", "3,2")
RZ = ("6,3", "10", "20")


def roughness(rng: random.Random, kind: PartKind) -> list[Requirement]:
    """The roughness of a surface the drawing names by a letter, GOST 2.309-73, 2.13; of the teeth of a gear."""
    if kind.sketch not in ("shaft", "gear", "disc", "housing") or rng.random() >= 0.3:
        return []
    if rng.random() < 0.75:
        value = rng.choice(RA)
        param = Param("roughness", f"Ra {value}", value=_f(value), unit="мкм")
        where = "рабочих поверхностей зубьев" if kind.sketch == "gear" else f"поверхности {rng.choice('БВГ')}"
        return [Requirement(f"Шероховатость {where} — √Ra {value}.", (param,))]
    value = rng.choice(RZ)
    param = Param("roughness", f"Rz {value}", value=_f(value), unit="мкм")
    return [Requirement(f"Шероховатость поверхностей {rng.choice('БВ')} и Г — √Rz {value}.", (param,))]


PLATINGS = ("Ц9.хр", "Ц15.хр", "Кд9.хр", "Хим.Окс.прм", "Хим.Фос.прм")
ENAMELS = ("Эмаль ПФ-115 серая", "Эмаль ПФ-115 зелёная", "Эмаль МЛ-12 серая")


def _plating(rng: random.Random, choices: tuple[str, ...] = PLATINGS) -> Requirement:
    coating = rng.choice(choices)
    text = f"Покрытие {coating}." if rng.random() < 0.8 else f"Покрытие {coating}, кроме поверхностей А."
    return Requirement(text, (Param("coating", coating),))


def _paint(rng: random.Random) -> Requirement:
    enamel = rng.choice(ENAMELS)
    match rng.randrange(3):
        case 0:
            text, first = f"Покрытие наружных необработанных поверхностей: Грунт ГФ-021, {enamel}.", "Грунт ГФ-021"
        case 1:
            text, first = f"Необработанные поверхности окрасить: Грунт ГФ-021, {enamel}.", "Грунт ГФ-021"
        case _:
            text, first = f"Покрытие: {enamel}.", " ".join(enamel.split()[:2])
    return Requirement(text, (Param("coating", first),))


def coating(rng: random.Random, kind: PartKind, material: Material) -> list[Requirement]:
    roll = rng.random()
    match material.grade:
        case "iron":
            return [_paint(rng)] if roll < 0.9 else []
        case "aluminium":
            if roll < 0.7:
                return [Requirement("Покрытие Ан.Окс.нхр.", (Param("coating", "Ан.Окс.нхр"),))]
            return [_paint(rng)] if roll < 0.9 else []
        case "mild":
            if kind.sketch == "bracket":
                return [_paint(rng)] if roll < 0.75 else [_plating(rng, ("Ц9.хр", "Ц15.хр"))] if roll < 0.9 else []
            return [_plating(rng, ("Ц9.хр", "Ц15.хр", "Хим.Окс.прм"))] if roll < 0.7 else []
        case "steel" | "case":
            share = {"nut": 0.8, "disc": 0.6, "shaft": 0.25}.get(kind.sketch, 0.1)
            return [_plating(rng)] if roll < share else []
    return []


# ---------------------------------------------------------------------------------------- the drawing

MARKING = Requirement("Маркировать обозначение детали.")


def part(rng: random.Random, kind: PartKind, material: Material) -> list[Requirement]:
    """The technical requirements of the drawing of a part of ``kind`` made of ``material``, in the order of GOST
    2.316-2008, 4.12."""
    items = heat_treatment(rng, kind, material)
    items += dimensions(rng, kind, material)
    items += roughness(rng, kind)
    items += coating(rng, kind, material)
    if rng.random() < 0.1:
        items.append(MARKING)
    return items


REFERENCE = Requirement("Размеры для справок.")


TORQUES = ((25, 30), (45, 50), (80, 90))  # N·m
PLAYS = ("0,05...0,10", "0,05...0,15", "0,1...0,2")  # mm


def assembly(rng: random.Random, bolts: str | None, bearings: bool, oil: str | None) -> list[Requirement]:
    """The technical requirements of an assembly drawing: dimensions for reference, the torque of the bolts at the
    position ``bolts``, the play of the bearings, the oil at the position ``oil``; no parameters of Block 2."""
    items = [REFERENCE if rng.random() < 0.7 else Requirement("* Размеры для справок.")]
    if bolts is not None and rng.random() < 0.5:
        low, high = rng.choice(TORQUES)
        items.append(Requirement(f"Момент затяжки болтов поз. {bolts} — {low}...{high} Н·м."))
    if bearings and rng.random() < 0.3:
        items.append(Requirement(f"Осевой люфт подшипников {rng.choice(PLAYS)} мм."))
    if oil is not None and rng.random() < 0.6:
        items.append(Requirement(f"Заправить маслом поз. {oil}."))
    return items
