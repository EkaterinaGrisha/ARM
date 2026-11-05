"""What counts as a correct extraction (plan item 2.4): one rule for every metric of ``eval/``.

The rule in words is in eval/gold/README.md, «Что считается верным извлечением». Text values are equal when they are
equal after :func:`norm`. A parameter of the truth is found when the core gives a parameter of the same category whose
name, qualifier or source text holds the name of the truth, one found parameter to one of the truth
(:func:`match_parameters`); its value is right when every number and the unit the truth gives are the same
(:func:`value_right`).
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
