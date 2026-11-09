"""Technical requirements block: the text above the title block, split into numbered items."""

from __future__ import annotations

import re

from kdcheck.config import TechRequirementsTemplate
from kdcheck.extract.title_block import Geometry
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import Line, lines_confidence
from kdcheck.schemas import Extracted


def requirement_lines(
    page: Page, geom: Geometry, block: TechRequirementsTemplate, *, numbered: bool = True
) -> list[Line]:
    """Requirement lines: the text block right above the title block. ``numbered``: only when one of its lines starts
    with the number of an item, as OCR reads it. The model reads the block again and needs no such sign (ADR-0023);
    without it the block ends at a row that starts ``row_indent`` right of the rows below: text of the field."""
    s = geom.s
    left, right = geom.x0 + block.left * s, geom.x0 + block.right * s
    candidates = [
        ln
        for ln in page.lines
        if not ln.vertical
        and ln.bbox[3] <= geom.y0 + block.bottom_tolerance * s
        and ln.bbox[0] >= left - block.left_reach * s
        and ln.cx <= right
        and ln.bbox[2] > left
    ]
    candidates.sort(key=lambda ln: -ln.bbox[3])
    picked: list[Line] = []
    edge = geom.y0
    for ln in candidates:
        gap = edge - ln.bbox[3]
        limit = (
            block.first_gap * s
            if not picked
            else max(block.line_gap * (ln.bbox[3] - ln.bbox[1]), block.min_line_gap * s)
        )
        if gap > limit:
            if picked:
                break
            continue
        picked.append(ln)
        edge = min(edge, ln.bbox[1])
    if not numbered:
        picked = _aligned(picked, block, s)
    picked.sort(key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    if numbered and not any(re.match(r"^\s*\S{0,2}\d{1,2}\s?[.)]\s", ln.text) for ln in picked):
        return []
    return picked


def block_rows(lines: list[Line], overlap: float) -> list[list[Line]]:
    """``lines`` joined into rows, top to bottom, each row left to right: OCR splits a row into pieces, and pieces of
    one row share ``overlap`` of the height of the lower one."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0])):
        if rows:
            top, bottom = min(p.bbox[1] for p in rows[-1]), max(p.bbox[3] for p in rows[-1])
            if min(ln.bbox[3], bottom) - max(ln.bbox[1], top) > overlap * min(ln.bbox[3] - ln.bbox[1], bottom - top):
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return [sorted(row, key=lambda p: p.bbox[0]) for row in rows]


def _aligned(lines: list[Line], block: TechRequirementsTemplate, s: float) -> list[Line]:
    """``lines`` by rows from the bottom up, to a row whose leftmost piece starts more than ``row_indent`` right of
    the rows below."""
    kept: list[Line] = []
    edge: float | None = None
    for row in reversed(block_rows(lines, block.row_overlap)):
        start = row[0].bbox[0]
        if edge is not None and start > edge + block.row_indent * s:
            break
        edge = start if edge is None else min(edge, start)
        kept += row
    return kept


# the number of an item: «2.» or «2)», never the start of «48...53 HRC» or «2,5 мм» that continues the item before
_ITEM_START = re.compile(r"^\s*(?:[il|]?\s?)?(\d{1,2})\s?(?:\.(?![.\d])|\))\s*")


def item_texts(texts: list[str], block: TechRequirementsTemplate) -> list[tuple[str, list[int]]]:
    """The items of a block from its lines in order: the text of each and the indices of its lines. A line that starts
    with a number starts an item, the others continue the one before; the heading is no item."""
    heading = re.compile(re.escape(block.heading), re.IGNORECASE)
    items: list[tuple[str, list[int]]] = []
    for i, raw in enumerate(texts):
        text = raw.strip()
        if not text or heading.fullmatch(text):
            continue
        m = _ITEM_START.match(text)
        if m or not items:
            items.append((text[m.end() :] if m else re.sub(r"^\S{1,3}\.\s", "", text), [i]))
        else:
            prev_text, prev_lines = items[-1]
            items[-1] = (f"{prev_text} {text}", prev_lines + [i])
    return items


def requirement_items(
    doc: LoadedDocument, page: Page, lines: list[Line], block: TechRequirementsTemplate
) -> list[Extracted]:
    result = []
    for text, indices in item_texts([ln.text for ln in lines], block):
        lns = [lines[i] for i in indices]
        bbox = (
            min(ln.bbox[0] for ln in lns),
            min(ln.bbox[1] for ln in lns),
            max(ln.bbox[2] for ln in lns),
            max(ln.bbox[3] for ln in lns),
        )
        raw = " ".join(ln.text for ln in lns)
        conf = lines_confidence(lns)
        result.append(Extracted(value=text.strip(), raw=raw, confidence=conf, source=doc.source(page, bbox, raw, conf)))
    return result
