"""ESKD designations, names and literas: search in text and normalization by ``dictionaries.yaml``.

Designations are found by the grammars of ``designations`` (ADR-0016), one per system of designation: the classifier
of GOST 2.201 first, then the systems of a maker. A kit is read by the grammars its file names show and by those always
on; with no designation in its file names, by all of them. Any grammar may be followed by an execution «-01» and a
document code of GOST 2.102. A file name may also carry a code its maker writes only there, «СП» for a specification:
it hints at the kind of the document and never becomes part of the designation.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass

from kdcheck.config import DIGIT, Dictionaries, Grammar

_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[\"'«»“”.,;:()\-–—_]+")
_BEFORE = r"(?<![\wА-Яа-яЁё])"
_AFTER = r"(?![\wА-Яа-яЁё])"
_LEAD = re.compile(r"[А-ЯЁA-Z\d][А-ЯЁA-Z\d.\-]*\d")  # the start of a file name that looks like a designation


@dataclass(frozen=True)
class _Compiled:
    grammar: Grammar
    text: re.Pattern[str]
    file_name: re.Pattern[str]  # the same grammar with the codes written only into file names


class Designations:
    """Compares designations, names and literas the same way across the doc set; ``words`` are its dictionaries."""

    def __init__(self, words: Dictionaries) -> None:
        self.words = words
        # Latin letters that look like Cyrillic ones, and letters that look like digits: frequent OCR and typing errors
        self._lat_to_cyr = str.maketrans(words.latin_lookalikes)
        self._cyr_digits = str.maketrans(words.digit_lookalikes)
        digit = r"[\d" + "".join(re.escape(c) for c in words.digit_lookalikes) + "]"
        text_codes = (*words.document_codes,)
        file_codes = (*words.document_codes, *words.filename_codes)
        self._grammars = [
            _Compiled(g, _compile(g, digit, text_codes), _compile(g, digit, file_codes)) for g in words.designations
        ]
        self._assembly_name = re.compile(rf"\b{re.escape(words.assembly_drawing_name)}\b")

    def normalize_designation(self, text: str | None, *, file_name: bool = False) -> str | None:
        """Bring the first designation of a text to the canonical form of its grammar, ``АБВГ.123456.789-01 СБ``.

        None if the text has none. ``file_name``: the text is a file name, so the codes written only there count.
        """
        if not text:
            return None
        found = self.find_designations(text, file_name=file_name)
        return found[0][0] if found else None

    @property
    def grammars(self) -> list[str]:
        """Names of the grammars in effect, in their order."""
        return [compiled.grammar.name for compiled in self._grammars]

    def for_kit(self, stems: list[str]) -> Designations:
        """The same rules with the grammars a kit is written in: those its file names show, and those always on.

        A kit is drawn in one system of designation, so a look of another system in its text is more likely a misread
        designation of its own, cut or split by OCR. When no file name holds a designation, every grammar stays on.
        """
        used = {self._grammars[order].grammar.name for stem in stems for _, order, _, _ in self._found(stem, True)}
        if not used:
            return self
        kit = copy.copy(self)
        kit._grammars = [c for c in self._grammars if c.grammar.always or c.grammar.name in used]
        return kit

    def find_designations(self, text: str, *, file_name: bool = False) -> list[tuple[str, int, int]]:
        """All designations in a string: (normalized, start, end), from left to right.

        Where the matches of two grammars overlap, the one that starts first wins, and at one start the earlier grammar.
        """
        return [(value, start, end) for value, _, start, end in self._found(text, file_name)]

    def file_code(self, stem: str) -> str | None:
        """The document code a file name writes after its first designation, a code written only into file names
        too: «АРМ.303411.000 СП Редуктор» → «СП»."""
        found = self._found(stem, True)
        if not found:
            return None
        _, order, start, _ = found[0]
        compiled = self._grammars[order]
        text = stem.replace("_", " ")
        text = text.upper().translate(self._lat_to_cyr) if compiled.grammar.upper else text.translate(self._lat_to_cyr)
        m = compiled.file_name.match(text, start)
        return m["code"] if m else None

    def _found(self, text: str, file_name: bool) -> list[tuple[str, int, int, int]]:
        """Designations of a string with the grammar of each: (normalized, grammar index, start, end).

        A file name may join its words by «_», «обозначение_наименование»: there it separates as a space does."""
        if file_name:
            text = text.replace("_", " ")
        cased = text.translate(self._lat_to_cyr)
        upper = text.upper().translate(self._lat_to_cyr)
        found: list[tuple[int, int, int, str]] = []
        for order, compiled in enumerate(self._grammars):
            pattern = compiled.file_name if file_name else compiled.text
            for m in pattern.finditer(upper if compiled.grammar.upper else cased):
                found.append((m.start(), order, m.end(), self._format(compiled.grammar, m)))
        result: list[tuple[str, int, int, int]] = []
        for start, order, end, value in sorted(found):
            if not result or start >= result[-1][3]:
                result.append((value, order, start, end))
        return result

    def _format(self, grammar: Grammar, m: re.Match[str]) -> str:
        parts = {name: value or "" for name, value in m.groupdict().items()}
        for name in grammar.numbers:
            parts[name] = parts[name].translate(self._cyr_digits)
        result = grammar.canonical.format_map(parts)
        if m["exec"]:
            result += f"-{m['exec']}"
        if m["code"] in self.words.document_codes:  # a code of the file name only is not part of the designation
            result += f" {m['code']}"
        return result

    def unknown_shape(self, stems: list[str], share: float) -> tuple[str, int] | None:
        """The look of the designations most file names start with, when no grammar knows them: «АААА.99.999», count.

        None unless such names are more than ``share`` of all. The look tells which grammar to add (ADR-0016); the core
        never reads designations by it.
        """
        looks: Counter[str] = Counter()
        for stem in stems:
            if self.find_designations(stem, file_name=True):
                continue
            lead = _LEAD.match(stem.upper().translate(self._lat_to_cyr))
            if lead:
                looks[re.sub(r"\d", "9", re.sub(r"[А-ЯЁA-Z]", "А", lead.group(0)))] += 1
        if not looks:
            return None
        look, count = looks.most_common(1)[0]
        return (look, count) if count > share * len(stems) else None

    def cyrillic(self, text: str) -> str:
        """``text`` with the Latin letters that look like Cyrillic ones made Cyrillic."""
        return text.translate(self._lat_to_cyr)

    def no_drawing(self, text: str | None, *, misread: bool = False) -> bool:
        """Whether the format column of a row holds «БЧ», a part made without a drawing (GOST 2.106). ``misread``:
        the cell was read by OCR, which reads the letters of the mark as digits; a format never reads so, as each
        one starts with «А»."""
        mark = self.words.no_drawing_mark
        cell = self.cyrillic((text or "").upper()).replace(" ", "")
        if cell == mark:
            return True
        if not misread or len(cell) != len(mark):
            return False
        misreads = self.words.no_drawing_misreads
        return all(c == m or c in misreads.get(m, "") for c, m in zip(cell, mark, strict=True))

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


def _compile(grammar: Grammar, digit: str, codes: tuple[str, ...]) -> re.Pattern[str]:
    tail = r"(?:\s?-\s?(?P<exec>\d{2,3}))?(?:\s?(?P<code>" + "|".join(re.escape(c) for c in codes) + r"))?"
    return re.compile(_BEFORE + "(?:" + grammar.pattern.replace(DIGIT, digit) + ")" + tail + _AFTER)


BASE_VARIANT = "–"  # the number of the base variant in the columns of a group specification of GOST 2.113
_VARIANT_NUMBER = re.compile(r"^[-–—]?\s*(\d{1,3})$")
_SHORTENED = re.compile(r"^[-–—]\s*(\d{2,3})\s*([А-ЯЁ]{1,4}\d?)?$")
_VARIANT_SUFFIX = re.compile(r"^(.+\S)-(\d{2,3})$")


def variant_number(text: str) -> str | None:
    """The number of a variant at the top of a column of quantities: «–» for the base one, «01», «02»…; None for any
    other text."""
    text = text.strip()
    if text and all(ch in "-–—" for ch in text):
        return BASE_VARIANT
    m = _VARIANT_NUMBER.match(text)
    return m.group(1).zfill(2) if m else None


def shortened(text: str) -> tuple[str, str | None] | None:
    """The number of a variant and the code of a document a row writes in place of the full designation below a row
    with it (GOST 2.113, 5.3): «-03» → («03», None), «-01 СБ» → («01», «СБ»); None for any other text."""
    m = _SHORTENED.match(text.strip())
    return (m.group(1), m.group(2)) if m else None


def split_variant(designation: str) -> tuple[str, str]:
    """The base designation of an item and the number of its variant, «–» for the base one:
    «АБВГ.545648.519-02» → («АБВГ.545648.519», «02»)."""
    m = _VARIANT_SUFFIX.match(base_designation(designation))
    return (m.group(1), m.group(2)) if m else (base_designation(designation), BASE_VARIANT)


def variant_designation(base: str, number: str) -> str:
    """The designation of the variant ``number`` of the item ``base``."""
    return base if number == BASE_VARIANT else f"{base}-{number}"


def base_designation(designation: str) -> str:
    """Item designation without the document code: ``АРМ.303411.000 СБ`` → ``АРМ.303411.000``."""
    return designation.split(" ")[0]


def doc_code(designation: str) -> str | None:
    parts = designation.split(" ", 1)
    return parts[1] if len(parts) == 2 else None


def digits(text: str) -> str:
    """The digits of a text in their order: what OCR keeps of a designation on a raster (ADR-0020)."""
    return "".join(ch for ch in text if ch.isdigit())


def one_digit_apart(a: str, b: str) -> bool:
    """Whether one of the strings is the other with one digit more: a digit dropped or doubled by OCR."""
    if abs(len(a) - len(b)) != 1:
        return False
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))
