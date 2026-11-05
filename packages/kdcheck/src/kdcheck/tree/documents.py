"""Stage 8: documents assembled from their sheets (ADR-0017).

A kit may keep a document of several sheets one sheet per file, the number of the sheet in brackets at the end of the
file name. Files are sheets of one document when they carry one designation, are of one kind and start at different
sheets. The first sheet keeps the specification or the part record of the document, with the rows and parameters of
every sheet; each value still names its own file in ``SourceRef.document_id``. Files that share a designation and a
sheet number stay apart: the rules report them as duplicates.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field

from kdcheck.schemas import DocumentInfo, Extracted, PartRecord, Specification, SpecRow

DRAWINGS = ("part_drawing", "assembly_drawing", "drawing")


@dataclass
class SheetSet:
    """A document over one file or several: its files in the order of their sheets."""

    designation: str
    files: list[DocumentInfo]
    sheets: Extracted | None  # «Листов» of the first sheet
    found: list[int] = field(default_factory=list)  # the numbers of the sheets in the kit
    apart: bool = False  # one of files that share a designation but not as sheets: the rules report a duplicate

    @property
    def first(self) -> DocumentInfo:
        return self.files[0]


@dataclass
class Assembly:
    specs: list[tuple[DocumentInfo, Specification]]
    parts: list[PartRecord]
    documents: list[SheetSet]
    log: list[str]


def _family(info: DocumentInfo) -> str | None:
    if info.kind == "specification":
        return "specification"
    return "drawing" if info.kind in DRAWINGS else None


def _pages(info: DocumentInfo) -> int:
    return max(1, sum(1 for p in info.pages if not p.problem))


def _numbers(files: list[DocumentInfo], later: set[str]) -> list[int] | None:
    """The first sheet of each file, a file without a number being sheet 1 when no other file is; None when two files
    start at one sheet, the sheets of one file run into the next, or a file past sheet 1 has no form 2a of a later sheet
    to say so: a copy of a file may get «(2)» in its name too."""
    if any(f.sheet is not None and f.sheet > 1 and f.id not in later for f in files):
        return None
    unnumbered = [f for f in files if f.sheet is None]
    taken = {f.sheet for f in files if f.sheet is not None}
    if len(unnumbered) > 1 or (unnumbered and 1 in taken):
        return None
    numbers = [f.sheet if f.sheet is not None else 1 for f in files]  # in the order of ``files``
    if len(set(numbers)) < len(numbers):
        return None
    starts = sorted((n, i) for i, n in enumerate(numbers))
    for (n, i), (m, _) in itertools.pairwise(starts):
        if n + _pages(files[i]) > m:
            return None
    return numbers


def assemble(
    infos: list[DocumentInfo],
    specs: list[tuple[DocumentInfo, Specification]],
    parts: list[PartRecord],
    later: set[str],
) -> Assembly:
    """The documents of the kit with their sheets, and the specifications and part records, one per document;
    ``later`` are the files whose title block is form 2a of a later sheet."""
    groups: defaultdict[tuple[str, str], list[DocumentInfo]] = defaultdict(list)
    for info in infos:
        family = _family(info)
        if family is not None and info.designation:
            groups[(family, info.designation)].append(info)
    for (family, designation), files in list(groups.items()):
        # later sheets of a drawing with no first sheet, under the designation of a specification of the kit: later
        # sheets of that specification whose table is of a form the core does not read
        if family == "drawing" and ("specification", designation) in groups and all(f.id in later for f in files):
            for f in files:
                f.kind = "specification"
                f.notes.append("Последующий лист спецификации: таблица этой формы не прочитана")
            groups[("specification", designation)] += files
            del groups[(family, designation)]
    spec_of = {doc.id: spec for doc, spec in specs}
    part_of = {p.document_id: p for p in parts}
    merged: set[str] = set()
    documents: list[SheetSet] = []
    log: list[str] = []
    for (family, designation), files in groups.items():
        if len(files) == 1:
            documents.append(_single(files[0], spec_of, part_of))
            continue
        starts = _numbers(files, later)
        if starts is None:
            names = ", ".join(f.path for f in files)
            log.append(
                f"Файлы {designation} не собраны в документ: номера листов повторяются, неизвестны или не подтверждены "
                f"основной надписью — {names}"
            )
            documents += [_single(f, spec_of, part_of, apart=True) for f in files]
            continue
        order = sorted(range(len(files)), key=starts.__getitem__)
        files = [files[i] for i in order]
        numbers = [starts[i] for i in order]
        first = files[0]
        for f, n in zip(files, numbers, strict=True):
            f.sheet = n
            if f is not first:
                f.sheet_of = first.id
                merged.add(f.id)
        if family == "specification":
            read = [spec_of[f.id] for f in files if f.id in spec_of]
            head = spec_of.get(first.id)
            if head is not None:
                head.rows = _rows(read)
            sheets = head.sheets if head is not None else None
        else:
            record = part_of.get(first.id)
            rest = [part_of[f.id] for f in files[1:] if f.id in part_of]
            if record is not None:
                for p in rest:
                    record.requirements += p.requirements
                    record.parameters += p.parameters
            sheets = record.title_block.sheets if record is not None else None
        found = [n + k for f, n in zip(files, numbers, strict=True) for k in range(_pages(f))]
        documents.append(SheetSet(designation, files, sheets, found))
    return Assembly(
        specs=[(doc, spec) for doc, spec in specs if doc.id not in merged],
        parts=[p for p in parts if p.document_id not in merged],
        documents=documents,
        log=log,
    )


def _rows(sheets: list[Specification]) -> list[SpecRow]:
    """The rows of the sheets in order; the rows a later sheet starts with before its first heading are in the section
    the sheet before it ends with, as a section runs on from sheet to sheet."""
    rows: list[SpecRow] = []
    for spec in sheets:
        section = rows[-1].section if rows else None
        leading = True
        for row in spec.rows:
            if row.section is not None:
                leading = False
            elif leading and section is not None:
                row.section = section
            rows.append(row)
    return rows


def _single(
    info: DocumentInfo,
    spec_of: dict[str, Specification],
    part_of: dict[str, PartRecord],
    *,
    apart: bool = False,
) -> SheetSet:
    if info.id in spec_of:
        sheets = spec_of[info.id].sheets
    else:
        part = part_of.get(info.id)
        sheets = part.title_block.sheets if part is not None else None
    start = info.sheet or 1
    return SheetSet(info.designation or "", [info], sheets, list(range(start, start + _pages(info))), apart)
