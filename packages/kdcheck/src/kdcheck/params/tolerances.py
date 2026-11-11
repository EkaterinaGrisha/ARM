"""Limit deviations of a size tolerance field by GOST 25346-2013 (ISO 286-1:2010): Ø40H7 → 0/+0,025.

``tolerances.yaml`` holds the standard tolerances IT1–IT18 and the fundamental deviations of shafts for sizes up to
500 mm. A shaft d–h has its upper deviation there, the lower one is IT below it; a shaft k–p has its lower one, the
upper one is IT above it; js is ±IT/2. A hole is derived from the shaft of its letter (GOST 25346, 5.3): A–H mirror
it, EI = −es; K, M and N up to IT8 and P up to IT7 take ES = −ei + Δ, where Δ is the difference of the grade and the
grade finer, and Δ = 0 up to 3 mm; finer grades take ES = −ei, N over IT8 ES = 0. A letter or a size the tables do
not hold, and K over IT8, which the standard does not set, get no deviations.
"""

from __future__ import annotations

import bisect
import re

from kdcheck.config import Tolerances

FIELD = re.compile(r"^(?P<letter>[A-Za-z]{1,2})(?P<grade>\d{1,2})$")
_MM = 1000  # µm in a millimetre
PLACES = 5  # IT1 halved is 0,4 µm: deviations keep tenths of a micrometre


def _half(it: float, grade: int) -> float:
    """IT/2 of js and JS; for js7–js11 an odd IT in whole micrometres goes to the even number below it, so that the
    deviations are whole micrometres, as the tables of GOST 25347 give them."""
    if 7 <= grade <= 11 and float(it).is_integer() and int(it) % 2:
        return (it - 1) / 2
    return it / 2


def deviations(field: str, size: float, table: Tolerances) -> tuple[float, float] | None:
    """The lower and the upper deviation of the tolerance field ``field`` («H7», «k6», «JS9») at the nominal size
    ``size``, both in mm; None when the tables hold no such field or size."""
    m = FIELD.match(field)
    if m is None or not 0 < size <= table.sizes[-1]:
        return None
    letter, grade = m["letter"], int(m["grade"])
    if letter == "Js":  # the symmetric field of a hole as drawings write it, «Js9»
        letter = "JS"
    if grade not in table.grades:
        return None
    band = bisect.bisect_left(table.sizes, size)
    it = table.grades[grade][band]
    found = _hole(letter, grade, band, it, table) if letter[0].isupper() else _shaft(letter, grade, band, it, table)
    if found is None:
        return None
    lower, upper = found
    return round(lower / _MM, PLACES) + 0.0, round(upper / _MM, PLACES) + 0.0  # + 0.0: no -0.0 of H


def _shaft(letter: str, grade: int, band: int, it: float, table: Tolerances) -> tuple[float, float] | None:
    if letter == "js":
        half = _half(it, grade)
        return -half, half
    if letter in table.upper:
        es = table.upper[letter][band]
        return es - it, es
    if letter in table.lower:
        low, high = table.k_grades
        ei = table.lower[letter][band] if letter != "k" or low <= grade <= high else 0
        return ei, ei + it
    return None


def _hole(letter: str, grade: int, band: int, it: float, table: Tolerances) -> tuple[float, float] | None:
    shaft = letter.lower()
    if letter == "JS":
        half = _half(it, grade)
        return -half, half
    if shaft in table.upper:
        ei = -table.upper[shaft][band]
        return ei, ei + it
    if shaft not in table.lower:
        return None
    up_to = 7 if shaft == "p" else 8  # the grades that take Δ
    finer = table.grades.get(grade - 1)
    delta = finer is not None and band > 0  # Δ = 0 up to 3 mm
    if grade <= up_to:
        es = -table.lower[shaft][band] + (it - finer[band] if delta and finer is not None else 0)
    elif shaft == "n":
        es = 0
    elif shaft == "k":
        return None
    else:
        es = -table.lower[shaft][band]
    return es - it, es
