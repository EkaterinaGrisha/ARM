"""Specification parsing (GOST 2.106, form 1).

Table columns are restored from the positions of the «Обозначение» and «Наименование» headers and the column widths
of ``forms.yaml``. Rows are grouped by the vertical coordinate; wrapped names join the previous position, and section
headers («Детали», «Стандартные изделия»…) set the section for the rows below. On a page image the table is found by
its ruled lines and read row by row (:mod:`kdcheck.ocr.cells`): its rows come by column, and the same rules make
positions of them.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from kdcheck.config import Forms, SpecificationTemplate
from kdcheck.extract import title_block
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import BBox, Word
from kdcheck.normalize.designations import Designations
from kdcheck.schemas import Extracted, Specification, SpecRow

Column = tuple[str, float, float]  # field, left and right edge, mm from the left edge of the table


@dataclass
class Table:
    x0: float
    s: float
    top: float
    bottom: float
    columns: list[Column]

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
        return Table(prev.x0, prev.s, top, page.height - template.continuation_bottom * prev.s, prev.columns)
    columns = [(name, left, right) for name, (left, right) in template.columns.model_dump().items()]
    designation_cx, name_cx = sum(template.columns.designation) / 2, sum(template.columns.name) / 2
    d, n = header
    s = (n.cx - d.cx) / (name_cx - designation_cx)
    low, high = template.scale_range
    if not low * page.mm < s < high * page.mm:
        s = page.mm
    x0 = d.cx - designation_cx * s
    top = max(d.bbox[3], n.bbox[3]) + template.header_gap * s
    geom = title_block.locate(page, forms)
    if geom is not None and geom.form == 2:
        bottom = geom.y0 - template.title_block_gap * s
    else:
        bottom = page.height - template.bottom_margin * s  # form 2a
    return Table(x0, s, top, bottom, columns)


def parse(doc: LoadedDocument, forms: Forms, eskd: Designations) -> Specification:
    spec = Specification(document_id=doc.id)
    rows: list[SpecRow] = []
    section: str | None = None
    table: Table | None = None
    sections = {re.sub(r"[^а-я]", "", s.lower()): s for s in eskd.words.spec_sections}
    for page in doc.pages:
        if not page.lines:
            continue
        if spec.designation is None:
            geom = title_block.locate(page, forms)
            if geom is not None and geom.form == 2:
                tb = title_block.parse(doc, page, geom, forms, eskd)
                spec.designation, spec.name, spec.litera, spec.revision = (
                    tb.designation,
                    tb.name,
                    tb.litera,
                    tb.revision,
                )
        ruled = page.regions.table if page.regions is not None else None
        if ruled is not None:  # read row by row by its ruled lines
            similarity = forms.specification.section_similarity
            section = _parse_rows(doc, page, ruled.rows, rows, section, sections, eskd, similarity)
            continue
        table = _table(page, find_header(page, forms.specification), table, forms)
        if table is None:
            continue
        section = _parse_page(doc, page, table, rows, section, forms.specification, sections, eskd)
    if spec.designation is None:
        spec.designation = _designation_fallback(doc, forms.specification, eskd)
    spec.rows = rows
    return spec


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
    section: str | None,
    template: SpecificationTemplate,
    sections: dict[str, str],
    eskd: Designations,
) -> str | None:
    left, right = (table.x0 + edge * table.s for edge in template.word_range)
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
    return _parse_rows(doc, page, clusters, rows, section, sections, eskd, template.section_similarity)


def _parse_rows(
    doc: LoadedDocument,
    page: Page,
    clusters: list[dict[str, list[Word]]],
    rows: list[SpecRow],
    section: str | None,
    sections: dict[str, str],
    eskd: Designations,
    similarity: float,
) -> str | None:
    """Positions of the rows of a page, each row as its words by column; a section header sets the section below."""
    for cells in clusters:
        if not cells:
            continue
        text = {k: " ".join(w.text for w in v) for k, v in cells.items()}
        only_name = set(cells) == {"name"}
        key = re.sub(r"[^а-я]", "", text.get("name", "").lower())
        # OCR misreads a header; a text layer does not, and its «деталь» is the second line of a name
        header = _section(key, sections, similarity if page.method == "ocr" else 1.0) if only_name else None
        if header is not None:
            section = header
            continue
        has_identity = any(text.get(k) for k in ("designation", "position", "quantity", "format"))
        if not has_identity and rows and rows[-1].source and rows[-1].source.page == page.number:
            _append_continuation(doc, page, rows[-1], cells)
            continue
        rows.append(_make_row(doc, page, len(rows) + 1, section, cells, eskd))
    return section


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
    doc: LoadedDocument, page: Page, index: int, section: str | None, cells: dict[str, list[Word]], eskd: Designations
) -> SpecRow:
    row = SpecRow(id=f"{doc.id}-r{index}", section=section)
    all_words = [w for ws in cells.values() for w in ws]
    for field, words in cells.items():
        raw = " ".join(w.text for w in words)
        value: str | float | None = raw
        if field == "designation":
            value = eskd.normalize_designation(raw) or raw
        elif field == "quantity":
            value = _quantity(raw)
        elif field == "position":
            m = re.search(r"\d+", raw)
            value = m.group(0) if m else raw
        setattr(row, field, Extracted(value=value, raw=raw, source=doc.source(page, _bbox(words), raw)))
    row.source = doc.source(page, _bbox(all_words), " | ".join(" ".join(w.text for w in ws) for ws in cells.values()))
    return row


def _append_continuation(doc: LoadedDocument, page: Page, row: SpecRow, cells: dict[str, list[Word]]) -> None:
    for field in ("name", "note"):
        words = cells.get(field)
        if not words:
            continue
        add = " ".join(w.text for w in words)
        current: Extracted | None = getattr(row, field)
        if current is None:
            setattr(row, field, Extracted(value=add, raw=add, source=doc.source(page, _bbox(words), add)))
            continue
        current.value = f"{current.value} {add}".strip()
        current.raw = f"{current.raw} {add}".strip()
        if current.source and current.source.bbox:
            x0, y0, x1, y1 = current.source.bbox
            nx0, ny0, nx1, ny1 = page.norm_bbox(_bbox(words))
            current.source.bbox = (min(x0, nx0), min(y0, ny0), max(x1, nx1), max(y1, ny1))


def _quantity(raw: str) -> float | str | None:
    m = re.search(r"\d+(?:[.,]\d+)?", raw)
    if not m:
        return raw or None
    return float(m.group(0).replace(",", "."))
