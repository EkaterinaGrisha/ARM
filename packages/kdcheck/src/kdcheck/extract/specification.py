"""Specification parsing (GOST 2.106, form 1; group ones of GOST 2.113 by variants A and B, ADR-0025, ADR-0026).

Table columns are restored from the positions of the «Обозначение» and «Наименование» headers and the column widths
of ``forms.yaml``. Rows are grouped by the vertical coordinate; wrapped names join the previous position, and section
headers («Детали», «Стандартные изделия»…) set the section for the rows below. On a page image the table is found by
its ruled lines and read row by row (:mod:`kdcheck.ocr.cells`): its rows come by column, and the same rules make
positions of them.

A group specification by variant B has a column of quantities for each variant instead of «Кол.», the number of the
variant at its top. On a text layer its form is told by these numbers in the header; on a page image, by its lines.
The quantities of a row go to ``quantities`` by the number of the variant. A row that writes «-03» below the full
designation of the same item names its variant 03 (GOST 2.113, 5.3).

A group specification by variant A takes form 1: the constant data first, then the heading «Переменные данные для
исполнений» and, under the designation of each variant, the rows of that variant with sections of their own (GOST
2.113, 5.5.1). These headings are told by their text, wherever the columns cut them, and each row below the
designation of a variant gets the number of that variant in ``variant``.
"""

from __future__ import annotations

import dataclasses
import difflib
import re
from dataclasses import dataclass

from kdcheck.config import Forms, GroupForm, SpecificationTemplate
from kdcheck.extract import title_block
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import BBox, Word
from kdcheck.normalize.designations import (
    BASE_VARIANT,
    Designations,
    shortened,
    split_variant,
    variant_designation,
    variant_number,
)
from kdcheck.schemas import Extracted, Specification, SpecRow

Column = tuple[str, float, float]  # field, left and right edge, mm from the left edge of the table
NO_QUANTITY = "×"  # a document of a group specification by variant B in the columns of the variants it serves


@dataclass
class _Rows:
    """Where the rows read so far stand: the section, and in a group specification by variant A the variable data."""

    section: str | None = None
    variable: bool = False  # below the heading «Переменные данные для исполнений»
    variant: str | None = None  # the number of the variant whose heading is above the rows
    headings: list[str] = dataclasses.field(default_factory=list)  # the numbers of the variants by their headings


@dataclass
class Table:
    x0: float
    s: float
    top: float
    bottom: float
    columns: list[Column]
    width: float = 0.0  # mm; 0 for the width of the title block
    variants: list[str] = dataclasses.field(default_factory=list)  # numbers of the variants of the columns «q0», «q1»…
    read: set[str] = dataclasses.field(default_factory=set)  # of them, those read at the tops of their columns

    def column(self, x: float) -> str | None:
        mm = (x - self.x0) / self.s
        for name, left, right in self.columns:
            if left <= mm < right:
                return name
        return None


def find_header(page: Page, template: SpecificationTemplate) -> tuple[Word, Word] | None:
    words = template.header_words
    lines = [ln for ln in page.lines if not ln.vertical]
    des = [w for ln in lines for w in ln.words if w.text.lower().startswith(words.designation)]
    names = [w for ln in lines for w in ln.words if w.text.lower().startswith(words.name)]
    for d in des:
        for n in names:
            if (
                abs(d.cy - n.cy) < template.header_row_tolerance * page.mm
                and n.cx > d.cx
                and d.cy < page.height * template.header_zone
            ):
                return d, n
    return None


def is_specification(page: Page, forms: Forms) -> bool:
    """A table of form 1 of GOST 2.106: by its ruled lines on a page image, else by the column headers."""
    if page.regions is not None and page.regions.table is not None:
        return True
    return find_header(page, forms.specification) is not None


def _table(page: Page, header: tuple[Word, Word] | None, prev: Table | None, forms: Forms) -> Table | None:
    template = forms.specification
    if header is None:
        if prev is None:
            return None
        top = page.height * template.continuation_top
        bottom = page.height - template.continuation_bottom * prev.s
        return Table(prev.x0, prev.s, top, bottom, prev.columns, prev.width, prev.variants, prev.read)
    d, n = header
    group = _group(page, header, template)
    if group is not None:
        form, s, subheads = group
        designation_cx = sum(form.columns.designation) / 2
        columns = [(name, left, right) for name, (left, right) in form.columns.model_dump().items()]
        v = form.variants
        spans = [(v.left + k * v.width, v.left + (k + 1) * v.width) for k in range(v.count)]
        columns += [(f"q{k}", left, right) for k, (left, right) in enumerate(spans)]
        x0 = d.cx - designation_cx * s
        texts = [" ".join(w.text for w in subheads if left <= (w.cx - x0) / s < right) for left, right in spans]
        numbers, read = variant_numbers(texts)
        top = max(d.bbox[3], n.bbox[3], *(w.bbox[3] for w in subheads)) + template.header_gap * s
        return Table(x0, s, top, _bottom(page, forms, s), columns, form.width, numbers, read)
    columns = [(name, left, right) for name, (left, right) in template.columns.model_dump().items()]
    designation_cx, name_cx = sum(template.columns.designation) / 2, sum(template.columns.name) / 2
    s = (n.cx - d.cx) / (name_cx - designation_cx)
    low, high = template.scale_range
    if not low * page.mm < s < high * page.mm:
        s = page.mm
    x0 = d.cx - designation_cx * s
    top = max(d.bbox[3], n.bbox[3]) + template.header_gap * s
    return Table(x0, s, top, _bottom(page, forms, s), columns)


def _bottom(page: Page, forms: Forms, s: float) -> float:
    geom = title_block.locate(page, forms)
    if geom is not None and geom.form == 2:
        return geom.y0 - forms.specification.title_block_gap * s
    return page.height - forms.specification.bottom_margin * s  # form 2a


def _group(
    page: Page, header: tuple[Word, Word], template: SpecificationTemplate
) -> tuple[GroupForm, float, list[Word]] | None:
    """The group form of GOST 2.113 whose numbers of variants stand in the header, its scale and these numbers; None
    for form 1 of GOST 2.106. The numbers are the words «–», «01»… right of the «Наименование» header and level with
    it; the leftmost one stands in the middle of the first column of quantities, so the form is the one that puts it
    at the scale of the page."""
    d, n = header
    reach = template.header_height / 2 * page.mm  # the numbers lie in the header, the rows below it
    subheads = [
        w
        for ln in page.lines
        if not ln.vertical
        for w in ln.words
        if w.cx > n.cx and abs(w.cy - d.cy) <= reach and variant_number(w.text) is not None
    ]
    if not subheads:
        return None
    first = min(subheads, key=lambda w: w.cx)
    low, high = template.scale_range
    best: tuple[GroupForm, float, list[Word]] | None = None
    for form in template.groups.values():
        middle = form.variants.left + form.variants.width / 2 - sum(form.columns.designation) / 2
        s = (first.cx - d.cx) / middle
        if low * page.mm < s < high * page.mm and (best is None or abs(s - page.mm) < abs(best[1] - page.mm)):
            best = (form, s, subheads)
    return best


def variant_numbers(subheads: list[str]) -> tuple[list[str], set[str]]:
    """The numbers of the variants of the columns of quantities from the texts at their tops, and those of them read.

    A column whose number is not read, or read again, gets the number after the one before it, the first one «–»:
    GOST 2.113 puts the base variant first and the others in order.
    """
    numbers: list[str] = []
    read: set[str] = set()
    for text in subheads:
        number = variant_number(text)
        if number is None or number in numbers:
            last = numbers[-1] if numbers else None
            number = BASE_VARIANT if last is None else f"{(0 if last == BASE_VARIANT else int(last)) + 1:02d}"
        else:
            read.add(number)
        numbers.append(number)
    return numbers, read


def parse(doc: LoadedDocument, forms: Forms, eskd: Designations) -> Specification:
    spec = Specification(document_id=doc.id)
    rows: list[SpecRow] = []
    state = _Rows()
    table: Table | None = None
    variants: dict[str, bool] = {}  # the numbers of the variants of the tables in order: read at the top of a column
    sections = {re.sub(r"[^а-я]", "", s.lower()): s for s in eskd.words.spec_sections}
    for page in doc.pages:
        if not page.lines:
            continue
        if spec.designation is None:
            geom = title_block.locate(page, forms)
            if geom is not None and geom.form == 2:
                tb = title_block.parse(doc, page, geom, forms, eskd)
                spec.designation, spec.name, spec.litera, spec.revision, spec.sheets = (
                    tb.designation,
                    tb.name,
                    tb.litera,
                    tb.revision,
                    tb.sheets,
                )
                spec.first_use = tb.first_use
        ruled = page.regions.table if page.regions is not None else None
        if ruled is not None:  # read row by row by its ruled lines
            similarity = forms.specification.section_similarity
            numbers, read = variant_numbers([" ".join(w.text for w in words) for words in ruled.variants])
            _note_variants(variants, numbers, read)
            _parse_rows(doc, page, ruled.rows, rows, state, sections, eskd, similarity, numbers)
            continue
        table = _table(page, find_header(page, forms.specification), table, forms)
        if table is None:
            continue
        _note_variants(variants, table.variants, table.read)
        _parse_page(doc, page, table, rows, state, forms, sections, eskd)
    if spec.designation is None:  # a later sheet kept in a file of its own names its document in form 2a
        first = next((p for p in doc.pages if p.lines), None)
        later = title_block.later_sheet(doc, first, forms, eskd) if first is not None else None
        spec.designation = later.designation if later is not None else None
    if spec.designation is None:
        spec.designation = _designation_fallback(doc, forms.specification, eskd)
    spec.rows = rows
    held = {number for row in rows for number in row.quantities}
    spec.variants = [number for number, read in variants.items() if read or number in held]
    spec.variants += [number for number in state.headings if number not in spec.variants]
    return spec


def _note_variants(variants: dict[str, bool], numbers: list[str], read: set[str]) -> None:
    for number in numbers:
        variants[number] = variants.get(number, False) or number in read


def _designation_fallback(doc: LoadedDocument, template: SpecificationTemplate, eskd: Designations) -> Extracted | None:
    """Specification designation from large text at the bottom of the first sheet."""
    for page in doc.pages[:1]:
        candidates = [
            (ln, d)
            for ln in page.lines
            if ln.cy > page.height * template.designation_zone
            for d, _, _ in eskd.find_designations(ln.text)
        ]
        if candidates:
            ln, d = max(candidates, key=lambda c: c[0].size)
            return Extracted(value=d, raw=ln.text, source=doc.source(page, ln.bbox, ln.text))
    return None


def _cluster_rows(words: list[Word], tol: float) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.cy):
        if rows and abs(rows[-1][-1].cy - w.cy) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def _parse_page(
    doc: LoadedDocument,
    page: Page,
    table: Table,
    rows: list[SpecRow],
    state: _Rows,
    forms: Forms,
    sections: dict[str, str],
    eskd: Designations,
) -> None:
    template = forms.specification
    wider = table.width - forms.title_block.width if table.width else 0.0  # a group form wider than form 1
    left, right = table.x0 + template.word_range[0] * table.s, table.x0 + (template.word_range[1] + wider) * table.s
    words = [
        w
        for ln in page.lines
        if not ln.vertical
        for w in ln.words
        if table.top <= w.cy <= table.bottom and left <= w.cx <= right
    ]
    clusters = []
    for cluster in _cluster_rows(words, tol=template.row_tolerance * table.s):
        cells: dict[str, list[Word]] = {}
        for w in sorted(cluster, key=lambda w: w.bbox[0]):
            col = table.column(w.cx)
            if col:
                cells.setdefault(col, []).append(w)
        clusters.append(cells)
    similarity = template.section_similarity
    _parse_rows(doc, page, clusters, rows, state, sections, eskd, similarity, table.variants)


def _parse_rows(
    doc: LoadedDocument,
    page: Page,
    clusters: list[dict[str, list[Word]]],
    rows: list[SpecRow],
    state: _Rows,
    sections: dict[str, str],
    eskd: Designations,
    similarity: float,
    variants: list[str],
) -> None:
    """Positions of the rows of a page, each row as its words by column; a section header sets the section below,
    the heading of a variant by variant A the variant. ``variants`` are the numbers of the variants of the columns
    «q0», «q1»… of a group specification by variant B."""
    # OCR misreads a header; a text layer does not, and its «деталь» is the second line of a name
    close = similarity if page.method == "ocr" else 1.0
    for cells in clusters:
        if not cells:
            continue
        text = {k: " ".join(w.text for w in v) for k, v in cells.items()}
        line = " ".join(text.values())
        if _same_letters(line, eskd.words.variable_data, close):
            state.variable, state.section = True, None
            continue
        if (number := _variant_heading(text, line, state, eskd)) is not None:
            state.variant, state.section = number, None
            if number not in state.headings:
                state.headings.append(number)
            continue
        only_name = set(cells) == {"name"}
        key = re.sub(r"[^а-я]", "", text.get("name", "").lower())
        header = _section(key, sections, close) if only_name else None
        if header is not None:
            state.section = header
            continue
        has_identity = any(text.get(k) for k in ("designation", "position", "quantity", "format")) or any(
            _variant_column(k) is not None for k in text
        )
        if not has_identity and rows and rows[-1].source and rows[-1].source.page == page.number:
            _append_continuation(doc, page, rows[-1], cells)
            continue
        row = _make_row(doc, page, len(rows) + 1, state.section, cells, eskd, variants)
        row.variant = state.variant
        _expand_shortened(row, rows, eskd)
        rows.append(row)


def _same_letters(text: str, wanted: str, similarity: float) -> bool:
    """Whether a line is the heading ``wanted``: the same Cyrillic letters, or as close as ``similarity`` on a line
    read by OCR."""
    key, target = (re.sub(r"[^а-я]", "", t.lower().replace("ё", "е")) for t in (text, wanted))
    if key == target:
        return True
    return similarity < 1 and bool(key) and difflib.SequenceMatcher(None, key, target).ratio() >= similarity


def _variant_heading(text: dict[str, str], line: str, state: _Rows, eskd: Designations) -> str | None:
    """The number of the variant whose designation heads the variable data below, by variant A of GOST 2.113; None
    for a line of any other kind.

    The heading is a designation of an item alone on its line, with no position. Below «Переменные данные для
    исполнений» it may stand in any column; before it, never in the column «Обозначение», where a row writes a
    designation whose name was not read."""
    if text.get("position") or (not state.variable and text.get("designation")):
        return None
    found = eskd.find_designations(line)
    if len(found) != 1:
        return None
    designation, start, end = found[0]
    rest = line[:start] + line[end:]
    if re.search(r"[\wА-Яа-яЁё]", rest) or " " in designation:  # more text, or the code of a document
        return None
    return split_variant(designation)[1]


def _variant_column(name: str) -> int | None:
    """The index of the column of quantities «q0», «q1»…; None for another column."""
    return int(name[1:]) if name.startswith("q") and name[1:].isdigit() else None


def _expand_shortened(row: SpecRow, rows: list[SpecRow], eskd: Designations) -> None:
    """The full designation of a row that writes it shortened; its reading stays in ``raw``."""
    ex = row.designation
    if ex is not None and (full := full_designation(str(ex.raw or ""), rows, eskd)) is not None:
        ex.value = full


def full_designation(text: str, above: list[SpecRow], eskd: Designations) -> str | None:
    """The designation a row names when it writes only «-03» or «-01 СБ»: that variant of the item of the nearest row
    of ``above`` with a full designation (GOST 2.113, 5.3); None for any other text or without such a row."""
    short = shortened(eskd.cyrillic(text.upper()))
    if short is None:
        return None
    full = next(
        (
            str(r.designation.value)
            for r in reversed(above)
            if r.designation is not None and eskd.normalize_designation(str(r.designation.value))
        ),
        None,
    )
    if full is None:
        return None
    number, code = short
    base, _ = split_variant(full)
    return variant_designation(base, number) + (f" {code}" if code else "")


def _section(key: str, sections: dict[str, str], similarity: float) -> str | None:
    """The section a header row names: the closest name at ``similarity`` or more."""
    close = difflib.get_close_matches(key, sections, n=1, cutoff=similarity)
    return sections[close[0]] if close else None


def _bbox(words: list[Word]) -> BBox:
    return (
        min(w.bbox[0] for w in words),
        min(w.bbox[1] for w in words),
        max(w.bbox[2] for w in words),
        max(w.bbox[3] for w in words),
    )


def _make_row(
    doc: LoadedDocument,
    page: Page,
    index: int,
    section: str | None,
    cells: dict[str, list[Word]],
    eskd: Designations,
    variants: list[str],
) -> SpecRow:
    row = SpecRow(id=f"{doc.id}-r{index}", section=section)
    all_words = [w for ws in cells.values() for w in ws]
    for field, words in cells.items():
        raw = " ".join(w.text for w in words)
        value: str | float | None = raw
        source = doc.source(page, _bbox(words), raw, engine=words[0].engine)
        column = _variant_column(field)
        if column is not None:
            if column < len(variants) and (quantity := _quantity(raw, eskd)) is not None:
                row.quantities[variants[column]] = Extracted(value=quantity, raw=raw, source=source)
            continue
        if field == "designation":
            value = eskd.normalize_designation(raw) or raw
        elif field == "quantity":
            value = _quantity(raw, eskd)
        elif field == "format" and eskd.no_drawing(raw, misread=page.method != "text_layer"):
            value = eskd.words.no_drawing_mark  # what OCR read stays in ``raw``
        elif field == "position":
            m = re.search(r"\d+", raw)
            value = m.group(0) if m else raw
        setattr(row, field, Extracted(value=value, raw=raw, source=source))
    text = " | ".join(" ".join(w.text for w in ws) for ws in cells.values())
    row.source = doc.source(page, _bbox(all_words), text, engine=all_words[0].engine if all_words else None)
    return row


def _append_continuation(doc: LoadedDocument, page: Page, row: SpecRow, cells: dict[str, list[Word]]) -> None:
    for field in ("name", "note"):
        words = cells.get(field)
        if not words:
            continue
        add = " ".join(w.text for w in words)
        current: Extracted | None = getattr(row, field)
        if current is None:
            source = doc.source(page, _bbox(words), add, engine=words[0].engine)
            setattr(row, field, Extracted(value=add, raw=add, source=source))
            continue
        current.value = f"{current.value} {add}".strip()
        current.raw = f"{current.raw} {add}".strip()
        if current.source and current.source.bbox:
            x0, y0, x1, y1 = current.source.bbox
            nx0, ny0, nx1, ny1 = page.norm_bbox(_bbox(words))
            current.source.bbox = (min(x0, nx0), min(y0, ny0), max(x1, nx1), max(y1, ny1))


def _quantity(raw: str, eskd: Designations) -> float | str | None:
    m = re.search(r"\d+(?:[.,]\d+)?", raw)
    if not m:
        cross = eskd.cyrillic(raw.strip().upper()) in {NO_QUANTITY, "X", "Х"}  # «×» of a document read as a letter
        return NO_QUANTITY if cross else (raw or None)
    return float(m.group(0).replace(",", "."))
