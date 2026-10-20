"""ESKD designations: search in text and normalization."""

from __future__ import annotations

import re

# Latin letters that look like Cyrillic ones: a frequent OCR and typing error
_LAT_TO_CYR = str.maketrans("ABCEHKMOPTXYabcekmoptxy", "АВСЕНКМОРТХУавсекмортху")
_CYR_DIGITS = str.maketrans({"О": "0", "о": "0", "З": "3", "б": "6"})

DOC_CODES = ("СБ", "ВО", "ТЧ", "ГЧ", "МЭ", "МЧ", "УЧ", "ЭЗ", "Э3", "ПЗ", "ТУ", "ВП", "ВД", "ПЭ3", "ТБ", "РР", "Д1")

DESIGNATION_RE = re.compile(
    r"(?<![\wА-Яа-яЁё])"
    r"(?P<org>[A-ZА-ЯЁ]{2,6})\s?[.\s]\s?"
    r"(?P<cls>[\dОоЗ]{6})\s?\.\s?"
    r"(?P<num>[\dОоЗ]{3})"
    r"(?:\s?-\s?(?P<exec>\d{2,3}))?"
    r"(?:\s?(?P<code>" + "|".join(DOC_CODES) + r"))?"
    r"(?![\wА-Яа-яЁё])"
)


def normalize_designation(text: str | None) -> str | None:
    """Bring a designation to the ``АБВГ.123456.789-01 СБ`` form; None if the text has none."""
    if not text:
        return None
    m = DESIGNATION_RE.search(text.upper().translate(_LAT_TO_CYR))
    if not m:
        return None
    return _format(m)


def _format(m: re.Match[str]) -> str:
    org = m["org"].translate(_LAT_TO_CYR)
    cls = m["cls"].translate(_CYR_DIGITS)
    num = m["num"].translate(_CYR_DIGITS)
    result = f"{org}.{cls}.{num}"
    if m["exec"]:
        result += f"-{m['exec']}"
    if m["code"]:
        result += f" {m['code']}"
    return result


def find_designations(text: str) -> list[tuple[str, int, int]]:
    """All designations in a string: (normalized, start, end)."""
    upper = text.upper().translate(_LAT_TO_CYR)
    return [(_format(m), m.start(), m.end()) for m in DESIGNATION_RE.finditer(upper)]


def base_designation(designation: str) -> str:
    """Item designation without the document code: ``АРМ.303411.000 СБ`` → ``АРМ.303411.000``."""
    return designation.split(" ")[0]


def doc_code(designation: str) -> str | None:
    parts = designation.split(" ", 1)
    return parts[1] if len(parts) == 2 else None


_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[\"'«»“”.,;:()\-–—_]+")


def normalize_name(name: str | None) -> str:
    """Name for comparison: ignores case, ё/е, punctuation and spaces."""
    if not name:
        return ""
    s = name.lower().replace("ё", "е").translate(_LAT_TO_CYR)
    s = _PUNCT.sub(" ", s)
    s = re.sub(r"\bсборочный чертеж\b", " ", s)
    return _SPACES.sub(" ", s).strip()


LITERA_ORDER = ["П", "Э", "Т", "О", "О1", "О2", "О3", "А", "Б"]


def normalize_litera(text: str | None) -> str | None:
    if not text:
        return None
    s = text.strip().upper().translate(_LAT_TO_CYR).replace(" ", "")
    if s.startswith("0"):
        s = "О" + s[1:]
    return s or None


def litera_rank(litera: str | None) -> int | None:
    """Litera order by GOST 2.103, higher is a later stage; None for a litera outside the scale."""
    lit = normalize_litera(litera)
    if lit in LITERA_ORDER:
        return LITERA_ORDER.index(lit)
    return None
