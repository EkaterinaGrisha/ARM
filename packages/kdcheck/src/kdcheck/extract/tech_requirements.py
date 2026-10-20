"""Technical requirements block: the text above the title block, split into numbered items."""

from __future__ import annotations

import re

from kdcheck.extract.title_block import Geometry
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import Line, lines_confidence
from kdcheck.schemas import Extracted


def requirement_lines(page: Page, geom: Geometry) -> list[Line]:
    """Requirement lines: the text block right above the title block."""
    s = geom.s
    left, right = geom.x0 - 10 * s, geom.x0 + 186 * s
    candidates = [
        ln
        for ln in page.lines
        if not ln.vertical
        and ln.bbox[3] <= geom.y0 + 1 * s
        and ln.bbox[0] >= left - 60 * s
        and ln.cx <= right
        and ln.bbox[2] > left
    ]
    candidates.sort(key=lambda ln: -ln.bbox[3])
    picked: list[Line] = []
    edge = geom.y0
    for ln in candidates:
        gap = edge - ln.bbox[3]
        limit = 25 * s if not picked else max(2.5 * (ln.bbox[3] - ln.bbox[1]), 4 * s)
        if gap > limit:
            if picked:
                break
            continue
        picked.append(ln)
        edge = min(edge, ln.bbox[1])
    picked.sort(key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    if not any(re.match(r"^\s*\S{0,2}\d{1,2}\s?[.)]\s", ln.text) for ln in picked):
        return []
    return picked


_ITEM_START = re.compile(r"^\s*(?:[il|]?\s?)?(\d{1,2})\s?[.)]\s*")


def requirement_items(doc: LoadedDocument, page: Page, lines: list[Line]) -> list[Extracted]:
    items: list[tuple[str, list[Line]]] = []
    for ln in lines:
        text = ln.text.strip()
        if re.fullmatch(r"(?i)технические требования", text):
            continue
        m = _ITEM_START.match(text)
        if m or not items:
            items.append((text[m.end() :] if m else re.sub(r"^\S{1,3}\.\s", "", text), [ln]))
        else:
            prev_text, prev_lines = items[-1]
            items[-1] = (f"{prev_text} {text}", prev_lines + [ln])
    result = []
    for text, lns in items:
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
