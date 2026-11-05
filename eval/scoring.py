"""What counts as a correct extraction (plan item 2.4): one rule for every metric of ``eval/``.

The rule in words is in eval/gold/README.md, «Что считается верным извлечением». Text values are equal when they are
equal after :func:`norm`. A parameter of the truth is found when the core gives a parameter of the same category whose
name, qualifier or source text holds the name of the truth, one found parameter to one of the truth
(:func:`match_parameters`); its value is right when every number and the unit the truth gives are the same
(:func:`value_right`). The region of a value is right when it points to the box of the truth and to no other
(:func:`region_right`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Protocol

from kdcheck.config import load_settings

# What looks the same on the sheet but differs as typed: Latin letters like Cyrillic ones (configs/dictionaries.yaml),
# dashes and quotes. The diameter sign is left out: OCR often drops it, and the size and the field name the dimension.
FOLD = str.maketrans(
    load_settings().dictionaries.latin_lookalikes
    | dict.fromkeys("‐‑‒–—−", "-")
    | dict.fromkeys("«»“”„", '"')
    | dict.fromkeys("Øø⌀", "")
)


def norm(value: object) -> str:
    """The value as compared: without case, «ё», decimal comma, spaces, Latin lookalikes, dashes, quotes, «Ø»."""
    if value is None:
        return ""
    text = str(value).translate(FOLD).lower().replace("ё", "е").replace(",", ".")
    return re.sub(r"\s+", "", text)


class Wanted(Protocol):
    """A parameter of the truth: ``synth.truth.ParameterTruth``."""

    category: str
    name: str
    value: float | None
    value_min: float | None
    value_max: float | None
    unit: str | None


class Found(Wanted, Protocol):
    """A parameter of the report: ``kdcheck.schemas.Parameter``."""

    qualifier: str | None
    text: str


def found(want: Wanted, got: Found) -> bool:
    """Whether ``got`` is the parameter ``want``: the same category, the name of the truth in its name, qualifier or
    source text."""
    return got.category == want.category and norm(want.name) in norm(f"{got.name} {got.qualifier or ''} {got.text}")


def match_parameters(wanted: Sequence[Wanted], got: Sequence[Found]) -> list[int | None]:
    """For each parameter of the truth, the index of the found parameter matched to it, or None: one to one,
    in order."""
    used: set[int] = set()
    matches: list[int | None] = []
    for want in wanted:
        index = next((i for i, g in enumerate(got) if i not in used and found(want, g)), None)
        if index is not None:
            used.add(index)
        matches.append(index)
    return matches


def has_value(want: Wanted) -> bool:
    """Whether the truth gives a number or a unit to check: the template of the item writes one."""
    return any(v is not None for v in (want.value, want.value_min, want.value_max, want.unit))


def _same(a: float | None, b: float | None) -> bool:
    return a is None if b is None else a is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def value_right(want: Wanted, got: Found) -> bool:
    """Whether the numbers and the unit of ``got`` are those of the truth; those the truth leaves out do not
    count."""
    pairs = [(want.value, got.value), (want.value_min, got.value_min), (want.value_max, got.value_max)]
    numbers = all(_same(g, w) for w, g in pairs if w is not None)
    unit = want.unit is None or norm(want.unit) == norm(got.unit)
    return numbers and unit


Region = tuple[int, tuple[float, float, float, float]]  # the page from 1 and x0, y0, x1, y1 as shares of the page


def _centre(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def _holds(bbox: tuple[float, float, float, float], point: tuple[float, float]) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def region_problem(want: Region, got: Region | None, others: Sequence[Region]) -> str | None:
    """Why the region ``got`` the report gives for a value is not the box ``want`` of the truth, or None when it is:
    on the same page, it holds the centre of ``want`` and the centre of no other box of the document, ``others``.
    The second condition tells a region of the value from one of the whole title block or of the page."""
    if got is None:
        return "нет области"
    if got[0] != want[0]:
        return "другая страница"
    if not _holds(got[1], _centre(want[1])):
        return "не там, где значение"
    if any(page == got[0] and box != want[1] and _holds(got[1], _centre(box)) for page, box in others):
        return "захватывает другое значение"
    return None


def region_right(want: Region, got: Region | None, others: Sequence[Region]) -> bool:
    """Whether the region ``got`` points to the box ``want`` of the truth and to no other, :func:`region_problem`."""
    return region_problem(want, got, others) is None
