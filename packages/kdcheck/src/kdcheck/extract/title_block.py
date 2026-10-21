"""Title block parsing (GOST 2.104): form 1 for drawings, form 2 for specifications.

The block is located by the column labels («Лит.», «Масса», «Масштаб», «Листов») and scaled by the distance
between them. Values are then read from cells by the form geometry of ``forms.yaml``, so parsing works the same way
for vector PDFs and recognized scans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from kdcheck.config import ChangesTable, Forms, MarginColumns, TitleBlockTemplate
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import BBox, Line, lines_confidence
from kdcheck.normalize.designations import Designations
from kdcheck.schemas import Extracted, TitleBlock

Point = tuple[float, float]


@dataclass
class Geometry:
    form: int  # 1: drawing, 2: specification
    x0: float
    y0: float
    s: float  # page units per mm
    anchored: bool
    width: float  # mm
    height: float  # mm
    pad: float  # mm added around each cell

    def rect(self, cell: BBox, pad: float | None = None) -> BBox:
        pad = self.pad if pad is None else pad
        x0, y0, x1, y1 = cell
        return (
            self.x0 + (x0 - pad) * self.s,
            self.y0 + (y0 - pad) * self.s,
            self.x0 + (x1 + pad) * self.s,
            self.y0 + (y1 + pad) * self.s,
        )

    @property
    def bbox(self) -> BBox:
        return self.rect((0, 0, self.width, self.height), pad=0)


def _key(word: str) -> str:
    return re.sub(r"[^а-яё]", "", word.lower())


def locate(page: Page, forms: Forms) -> Geometry | None:
    """Locate the title block on the page; None if there are no column labels."""
    template = forms.title_block
    labels = template.label_words
    zone_x, zone_y = template.label_zone
    found: dict[str, list[Point]] = {}
    for line in page.lines:
        if line.vertical:
            continue
        for w in line.words:
            k = _key(w.text)
            if k in labels and w.cx > page.width * zone_x and w.cy > page.height * zone_y:
                found.setdefault(k, []).append((w.cx, w.cy))
    for form, spec in template.forms.items():
        hits = _best_row(found, spec.labels, tol=template.label_row_tolerance * page.mm)
        if len(hits) >= spec.min_labels or (spec.sufficient_label is not None and spec.sufficient_label in hits):
            return _geometry(page, template, form, hits)
    return None


def _best_row(found: dict[str, list[Point]], anchors: dict[str, Point], tol: float) -> dict[str, Point]:
    """Labels on one row; the row with the most matches wins."""
    best: dict[str, Point] = {}
    for key in anchors:
        for cx, cy in found.get(key, []):
            row = {key: (cx, cy)}
            for other in anchors:
                if other == key:
                    continue
                for ox, oy in found.get(other, []):
                    if abs(oy - cy) < tol and (ox - cx) * (anchors[other][0] - anchors[key][0]) > 0:
                        row[other] = (ox, oy)
                        break
            if len(row) > len(best):
                best = row
    return best


def _geometry(page: Page, template: TitleBlockTemplate, form: int, hits: dict[str, Point]) -> Geometry:
    anchors = template.forms[form].labels
    s = page.mm
    keys = sorted(hits, key=lambda k: anchors[k][0])
    if len(keys) >= 2:  # two labels give the scale
        a, b = keys[0], keys[-1]
        est = (hits[b][0] - hits[a][0]) / (anchors[b][0] - anchors[a][0])
        low, high = template.scale_range
        if low * s < est < high * s:
            s = est
    k = keys[-1]
    x0 = hits[k][0] - anchors[k][0] * s
    y0 = hits[k][1] - anchors[k][1] * s
    return _placed(template, form, x0, y0, s, anchored=True)


def _placed(template: TitleBlockTemplate, form: int, x0: float, y0: float, s: float, *, anchored: bool) -> Geometry:
    height = template.forms[form].height
    return Geometry(form, x0, y0, s, anchored, width=template.width, height=height, pad=template.cell_padding)


def default_geometry(page: Page, forms: Forms, form: int = 1) -> Geometry:
    """The block where the form puts it: in the bottom right corner of the frame."""
    template, margin, s = forms.title_block, forms.sheet.frame_margin, page.mm
    x0 = page.width - (margin + template.width) * s
    y0 = page.height - (margin + template.forms[form].height) * s
    return _placed(template, form, x0, y0, s, anchored=False)


def lines_in(page: Page, rect: BBox, row_height: float, *, vertical: bool = False) -> list[Line]:
    """Lines centred in ``rect``, in reading order by rows ``row_height`` page units high."""
    x0, y0, x1, y1 = rect
    picked = [ln for ln in page.lines if ln.vertical == vertical and x0 <= ln.cx <= x1 and y0 <= ln.cy <= y1]
    return sorted(picked, key=lambda ln: (round(ln.cy / row_height), ln.bbox[0]))


def _union(lines: list[Line]) -> BBox:
    return (
        min(ln.bbox[0] for ln in lines),
        min(ln.bbox[1] for ln in lines),
        max(ln.bbox[2] for ln in lines),
        max(ln.bbox[3] for ln in lines),
    )


def _extracted(
    doc: LoadedDocument, page: Page, lines: list[Line], value: str | int | None, confidence: float | None = None
) -> Extracted | None:
    if not lines or value in (None, ""):
        return None
    raw = " ".join(ln.text for ln in lines)
    confidence = confidence if confidence is not None else lines_confidence(lines)
    return Extracted(
        value=value, raw=raw, confidence=confidence, source=doc.source(page, _union(lines), raw, confidence)
    )


def _cell_text(lines: list[Line], labels: set[str]) -> str:
    words = [w.text for ln in lines for w in ln.words if _key(w.text) not in labels]
    return " ".join(words).strip()


def parse(doc: LoadedDocument, page: Page, geom: Geometry, forms: Forms, eskd: Designations) -> TitleBlock:
    template = forms.title_block
    row, labels = template.line_row_height, template.label_words
    cells = template.forms[geom.form].cells.model_dump(exclude_none=True)
    tb = TitleBlock()
    got: dict[str, list[Line]] = {name: lines_in(page, geom.rect(cell), row) for name, cell in cells.items()}

    # Designation: the largest line with an ESKD designation in column 2
    des_lines = got["designation"]
    candidates = [(ln, d) for ln in des_lines for d, _, _ in eskd.find_designations(ln.text)]
    if not candidates:  # the designation sometimes sticks out of its cell
        wide = lines_in(page, geom.rect(template.designation_overflow), row)
        candidates = [(ln, d) for ln in wide for d, _, _ in eskd.find_designations(ln.text)]
    if candidates:
        ln, d = max(candidates, key=lambda c: c[0].size)
        tb.designation = _extracted(doc, page, [ln], d)

    name_lines = [ln for ln in got["name"] if not eskd.find_designations(ln.text)]
    name = _cell_text(name_lines, labels)
    name = re.sub(r"\s+", " ", name)
    tb.name = _extracted(doc, page, name_lines, name or None)

    if "material" in got:
        mat = _cell_text(got["material"], labels)
        tb.material = _extracted(doc, page, got["material"], mat or None)
    if "mass" in got:
        m = re.search(r"\d+(?:[.,]\d+)?", _cell_text(got["mass"], labels))
        tb.mass = _extracted(doc, page, got["mass"], m.group(0) if m else None)
    if "scale" in got:
        m = re.search(r"\d+(?:[.,]\d+)?\s?:\s?\d+(?:[.,]\d+)?", _cell_text(got["scale"], labels))
        tb.scale = _extracted(doc, page, got["scale"], m.group(0).replace(" ", "") if m else None)

    lit = _cell_text(got["litera"], labels)
    lit_m = re.search(r"[ПЭТОИАБ0OAБ]\s?\d?", lit.upper())
    tb.litera = _extracted(doc, page, got["litera"], eskd.normalize_litera(lit_m.group(0)) if lit_m else None)

    for key in ("sheet", "sheets"):
        m = re.search(r"\d+", _cell_text(got[key], labels))
        setattr(tb, key, _extracted(doc, page, got[key], int(m.group(0)) if m else None))

    tb.revision = _revision(doc, page, geom, template.changes, row)
    margin = template.margin
    tb.archive_number = _margin_value(doc, page, margin.archive_label, margin, eskd)
    tb.first_use = _margin_value(doc, page, margin.first_use_label, margin, eskd)
    return tb


def _revision(doc: LoadedDocument, page: Page, geom: Geometry, changes: ChangesTable, row: float) -> Extracted | None:
    """Last change number: the largest number in the «Изм.» column above the header row."""
    s = geom.s
    header_y = geom.y0 + changes.header_y * s
    col = (geom.x0 + changes.column[0] * s, geom.x0 + changes.column[1] * s)
    for ln in lines_in(page, geom.rect(changes.label_area), row):
        for w in ln.words:
            if _key(w.text) == changes.label:
                header_y = w.cy
                col = (w.bbox[0] + changes.label_column[0] * s, w.bbox[0] + changes.label_column[1] * s)
                break
    best: tuple[int, Line] | None = None
    for ln in page.lines:
        if ln.vertical or not (geom.y0 - changes.top_tolerance * s <= ln.cy < header_y - changes.header_gap * s):
            continue
        for w in ln.words:
            if col[0] <= w.cx <= col[1] and re.fullmatch(r"\d{1,3}", w.text):
                num = int(w.text)
                if best is None or num > best[0]:
                    best = (num, ln)
    if best is None:
        return None
    num, ln = best
    entry = [
        x
        for x in page.lines
        if not x.vertical
        and abs(x.cy - ln.cy) < changes.row_tolerance * s
        and x.bbox[0] < geom.x0 + changes.row_right * s
    ]
    raw = " ".join(x.text for x in sorted(entry, key=lambda x: x.bbox[0]))
    return Extracted(value=num, raw=raw, source=doc.source(page, _union(entry), raw, lines_confidence(entry)))


def _margin_value(
    doc: LoadedDocument, page: Page, label_key: str, margin: MarginColumns, eskd: Designations
) -> Extracted | None:
    """Value of a vertical column in the left margin (archive number, first use)."""
    labels = [ln for ln in page.lines if ln.vertical and label_key in ln.text.lower()]
    for label in labels:
        lx0, ly0, lx1, ly1 = label.bbox
        best = None
        for ln in page.lines:
            if not ln.vertical or ln is label:
                continue
            if not (lx1 - margin.label_slack <= ln.bbox[0] <= lx1 + margin.value_reach * page.mm):
                continue
            overlap = min(ly1, ln.bbox[3]) - max(ly0, ln.bbox[1])
            if overlap > margin.min_overlap * min(ly1 - ly0, ln.bbox[3] - ln.bbox[1]):
                best = ln
                break
        if best is not None:
            value = eskd.find_designations(best.text)
            text = value[0][0] if value else best.text.strip()
            return Extracted(value=text, raw=best.text, source=doc.source(page, best.bbox, best.text, best.confidence))
    return None
