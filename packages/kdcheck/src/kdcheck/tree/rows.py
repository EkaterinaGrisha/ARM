"""Stage 8a: designations of specification rows read by OCR checked against the designations of the kit (ADR-0020).

OCR loses the letters of a designation on a raster, the code of the maker most of all, and drops a digit of a thin
«1» now and then, while the digits mostly stay. The designations of the documents of the kit, from their title blocks
and their file names, are the closed dictionary a row can name. A row read by one of ``row_designations.methods``
whose designation is not in the dictionary gets the one designation of the dictionary with the same digits, or
with one digit more or less. A digit read as another one is never forgiven: designations of neighbouring documents
differ by one digit, and a document missing from the kit must stay missing. The row keeps its reading in ``raw`` and
its cell as the evidence, gets the ``conflict`` status, and the specification a note with both.
"""

from __future__ import annotations

import re
from collections import defaultdict

from kdcheck.config import RowDesignations
from kdcheck.normalize.designations import Designations, base_designation, digits, doc_code, one_digit_apart
from kdcheck.schemas import DocumentInfo, Specification

_SEPARATORS = re.compile(r"[\s.,\-–—_]+")


def check_rows(
    specs: list[tuple[DocumentInfo, Specification]], kit: set[str], eskd: Designations, rules: RowDesignations
) -> int:
    """Give the rows of ``specs`` the designations of ``kit`` they were read for; return how many rows changed."""
    known = kit | {base_designation(d) for d in kit}
    by_digits: dict[str, set[str]] = defaultdict(set)
    for designation in known:
        if (found := digits(base_designation(designation))) and len(found) >= rules.min_digits:
            by_digits[found].add(base_designation(designation))
    changed = 0
    for info, spec in specs:
        for row in spec.rows:
            ex = row.designation
            if ex is None or ex.source is None or ex.source.method not in rules.methods:
                continue
            document = eskd.words.spec_sections.get(row.section or "") == "document"
            if str(ex.value) in (kit if document else known):  # a row of «Документация» names a document with its code
                continue
            target = _target(str(ex.value), document, known, by_digits, eskd, rules)
            if target is None or target == str(ex.value):
                continue
            reading = ex.raw or str(ex.value)
            ex.value, ex.status = target, "conflict"
            position = f"поз. {row.position.value}" if row.position is not None and row.position.value else row.id
            info.notes.append(f"Спецификация, строка {position}: прочитано «{reading}», в комплекте «{target}»")
            changed += 1
    return changed


def _target(
    value: str,
    document: bool,
    known: set[str],
    by_digits: dict[str, set[str]],
    eskd: Designations,
    rules: RowDesignations,
) -> str | None:
    """The designation of the kit the row was read for, or None when there is none or more than one. ``value`` is
    the reading as the grammars normalized it: a code of a document they gave back, «С5» read for «СБ», is kept."""
    words = _SEPARATORS.sub(" ", eskd.cyrillic(value.upper())).split()
    code = words[-1] if len(words) > 1 and words[-1] in eskd.words.document_codes else None
    read = digits(" ".join(words[:-1] if code else words))
    if len(read) < rules.min_digits:
        return None
    bases = by_digits.get(read, set())
    if not bases and rules.one_digit:
        bases = {b for d, group in by_digits.items() if one_digit_apart(read, d) for b in group}
    if len(bases) != 1:
        return None
    (base,) = bases
    if code is None and document:  # a row of «Документация» names «… СБ», never the item
        coded = sorted(d for d in known if base_designation(d) == base and doc_code(d))
        return coded[0] if len(coded) == 1 else None
    return f"{base} {code}" if code else base
