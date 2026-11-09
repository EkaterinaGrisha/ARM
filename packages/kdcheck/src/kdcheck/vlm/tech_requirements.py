"""The technical requirements block of a raster or a scan read by a vision-language model (ADR-0023).

OCR finds the lines of the block above the title block, but reads them poorly: «р.» for «2.», «Нецказанные» for
«Неуказанные». The block is the rectangle of those lines, taken without the check that one starts with the number of
an item, which OCR fails on most. The image the page was read from is cut there, brought to the resolution the model
reads best, and the model writes the text of the block line by line. The text is split into items by the same rule as
the lines of OCR (:func:`kdcheck.extract.tech_requirements.item_texts`). Each line of the answer is matched to a row of
the lines OCR found, in their order and by the likeness of their text, so the evidence of an item is the rows of its
lines; an item none of whose lines matches has the rectangle of the block.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from PIL import Image

from kdcheck.config import Settings
from kdcheck.extract.tech_requirements import block_rows, item_texts
from kdcheck.ingest.document import MM_PER_INCH, LoadedDocument, Page
from kdcheck.ingest.text import Line
from kdcheck.schemas import Extracted, SourceRef
from kdcheck.vlm.client import VlmClient

# The image the page was read from, where it sits on the page and page units per pixel, as ``page_raster`` gives.
Raster = tuple[Image.Image, tuple[float, float], float]


def read_block(
    doc: LoadedDocument, page: Page, lines: list[Line], *, client: VlmClient, raster: Raster, settings: Settings
) -> list[Extracted]:
    """The items of the block whose OCR lines are ``lines``, as the model reads them; none when it sees no text.

    A server that stops answering raises the fatal :class:`kdcheck.vlm.client.VlmError`, a reply in a wrong form the
    other one: the caller keeps the items of OCR.
    """
    rules = settings.models.vlm.requirements
    pad = rules.pad_mm * page.mm
    rect = (
        max(0.0, min(ln.bbox[0] for ln in lines) - pad),
        max(0.0, min(ln.bbox[1] for ln in lines) - pad),
        min(page.width, max(ln.bbox[2] for ln in lines) + pad),
        min(page.height, max(ln.bbox[3] for ln in lines) + pad),
    )
    text = client.read(_crop(raster, rect, page.mm, rules.target_dpi), settings.forms.tech_requirements.heading)
    if not text:
        return []
    confidence = settings.ingest.confidence.vlm
    texts = [t for t in text.splitlines() if t.strip()]
    rows = _rows(lines, settings.forms.tech_requirements.row_overlap)
    matched = _match(texts, [r[0] for r in rows], rules.line_match)
    items = []
    for item, indices in item_texts(texts, settings.forms.tech_requirements):
        boxes = [rows[j][1] for i in indices if (j := matched[i]) is not None]
        bbox = _union(boxes) if boxes else rect
        raw = " ".join(texts[i].strip() for i in indices)
        source = SourceRef(
            document_id=doc.id,
            page=page.number,
            bbox=page.norm_bbox(bbox),
            text=raw,
            method="vlm",
            confidence=confidence,
            engine=client.engine.model,
            engine_version=client.version,
        )
        items.append(Extracted(value=item, raw=raw, confidence=confidence, source=source))
    return items


BBox = tuple[float, float, float, float]


def _union(boxes: list[BBox]) -> BBox:
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def _rows(lines: list[Line], overlap: float) -> list[tuple[str, BBox]]:
    """The rows of the block OCR read, top to bottom: their text and their rectangle."""
    return [(" ".join(p.text for p in row), _union([p.bbox for p in row])) for row in block_rows(lines, overlap)]


def _match(answer: list[str], rows: list[str], least: float) -> list[int | None]:
    """For each line of the answer, the row of OCR it reads, or None: the alignment of the two in order that has the
    largest sum of likeness, a pair less alike than ``least`` being no pair."""

    def like(a: str, b: str) -> float:
        ratio = SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()
        return ratio if ratio >= least else 0.0

    n, m = len(answer), len(rows)
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = max(best[i + 1][j], best[i][j + 1], like(answer[i], rows[j]) + best[i + 1][j + 1])
    found: list[int | None] = [None] * n
    i = j = 0
    while i < n and j < m:
        score = like(answer[i], rows[j])
        if score > 0 and best[i][j] == score + best[i + 1][j + 1]:
            found[i] = j
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return found


def _crop(raster: Raster, rect: tuple[float, float, float, float], mm: float, target_dpi: int) -> Image.Image:
    """The rectangle of the page on its image, enlarged to ``target_dpi`` when the image is coarser."""
    image, (ox, oy), scale = raster
    x0, y0, x1, y1 = rect
    box = (round((x0 - ox) / scale), round((y0 - oy) / scale), round((x1 - ox) / scale), round((y1 - oy) / scale))
    crop = image.convert("L").crop(box)
    dpi = MM_PER_INCH * mm / scale  # ``mm``: page units per mm, ``scale``: page units per pixel
    k = target_dpi / dpi
    if k <= 1:
        return crop
    return crop.resize((round(crop.width * k), round(crop.height * k)), Image.Resampling.LANCZOS)
