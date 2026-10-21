"""ESKD designations, names and literas: search in text and normalization by ``dictionaries.yaml``."""

from __future__ import annotations

import re

from kdcheck.config import Dictionaries

_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[\"'«»“”.,;:()\-–—_]+")


class Designations:
    """Compares designations, names and literas the same way across the doc set; ``words`` are its dictionaries."""

    def __init__(self, words: Dictionaries) -> None:
        self.words = words
        # Latin letters that look like Cyrillic ones, and letters that look like digits: frequent OCR and typing errors
        self._lat_to_cyr = str.maketrans(words.latin_lookalikes)
        self._cyr_digits = str.maketrans(words.digit_lookalikes)
        digit = r"[\d" + "".join(re.escape(c) for c in words.digit_lookalikes) + "]"
        self._pattern = re.compile(
            r"(?<![\wА-Яа-яЁё])"
            r"(?P<org>[A-ZА-ЯЁ]{2,6})\s?[.\s]\s?"
            rf"(?P<cls>{digit}{{6}})\s?\.\s?"
            rf"(?P<num>{digit}{{3}})"
            r"(?:\s?-\s?(?P<exec>\d{2,3}))?"
            r"(?:\s?(?P<code>" + "|".join(re.escape(c) for c in words.document_codes) + r"))?"
            r"(?![\wА-Яа-яЁё])"
        )
        self._assembly_name = re.compile(rf"\b{re.escape(words.assembly_drawing_name)}\b")

    def normalize_designation(self, text: str | None) -> str | None:
        """Bring a designation to the ``АБВГ.123456.789-01 СБ`` form; None if the text has none."""
        if not text:
            return None
        m = self._pattern.search(text.upper().translate(self._lat_to_cyr))
        if not m:
            return None
        return self._format(m)

    def _format(self, m: re.Match[str]) -> str:
        org = m["org"].translate(self._lat_to_cyr)
        cls = m["cls"].translate(self._cyr_digits)
        num = m["num"].translate(self._cyr_digits)
        result = f"{org}.{cls}.{num}"
        if m["exec"]:
            result += f"-{m['exec']}"
        if m["code"]:
            result += f" {m['code']}"
        return result

    def find_designations(self, text: str) -> list[tuple[str, int, int]]:
        """All designations in a string: (normalized, start, end)."""
        upper = text.upper().translate(self._lat_to_cyr)
        return [(self._format(m), m.start(), m.end()) for m in self._pattern.finditer(upper)]

    def normalize_name(self, name: str | None) -> str:
        """Name for comparison: ignores case, ё/е, punctuation and spaces."""
        if not name:
            return ""
        s = name.lower().replace("ё", "е").translate(self._lat_to_cyr)
        s = _PUNCT.sub(" ", s)
        s = self._assembly_name.sub(" ", s)
        return _SPACES.sub(" ", s).strip()

    def normalize_litera(self, text: str | None) -> str | None:
        if not text:
            return None
        s = text.strip().upper().translate(self._lat_to_cyr).replace(" ", "")
        if s.startswith("0"):
            s = "О" + s[1:]
        return s or None

    def litera_rank(self, litera: str | None) -> int | None:
        """Litera order by GOST 2.103, higher is a later stage; None for a litera outside the scale."""
        lit = self.normalize_litera(litera)
        if lit in self.words.litera_order:
            return self.words.litera_order.index(lit)
        return None

    def is_assembly_drawing(self, designation: str | None, name: str | None) -> bool:
        """An assembly drawing by its document code or by its name."""
        if designation and doc_code(designation) == self.words.assembly_drawing_code:
            return True
        return self.words.assembly_drawing_name in (name or "").lower().replace("ё", "е")


def base_designation(designation: str) -> str:
    """Item designation without the document code: ``АРМ.303411.000 СБ`` → ``АРМ.303411.000``."""
    return designation.split(" ")[0]


def doc_code(designation: str) -> str | None:
    parts = designation.split(" ", 1)
    return parts[1] if len(parts) == 2 else None
