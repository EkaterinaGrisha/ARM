"""Cells of specification rows of rasters and scans read by a vision-language model (ADR-0021).

The table of a raster is placed by its ruled lines (:mod:`kdcheck.layout.raster`), so each row is a band and each
column of GOST 2.106 a known width. The image the page was read from is placed again the same way, and the cells of
``vlm.rows.fields`` of every band OCR read a row in are cut and given to the model, as the cells of a title block are
(:mod:`kdcheck.vlm.title_block`). The reading of the model is normalized by the rules that normalize OCR text and
compared with the row: an empty cell gets it, a different one is a conflict decided by ``vlm.conflict``.
"""

from __future__ import annotations

from collections.abc import Callable

from kdcheck.config import Settings
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.layout import raster
from kdcheck.normalize.designations import Designations
from kdcheck.schemas import Extracted, SourceRef, SpecRow
from kdcheck.vlm.client import VlmClient, VlmError
from kdcheck.vlm.title_block import Raster

BBox = tuple[float, float, float, float]
Place = Callable[[float, float], tuple[float, float]]  # a pixel of the straightened image → a page point


def read_rows(
    doc: LoadedDocument,
    page: Page,
    rows: list[SpecRow],
    *,
    client: VlmClient,
    image: Raster,
    settings: Settings,
    eskd: Designations,
) -> list[str]:
    """Read the cells of ``vlm.rows.fields`` of the rows of ``page`` into ``rows``; return notes for the document.

    A reply in a wrong form costs one cell; a server that stops answering raises the fatal :class:`VlmError`.
    """
    picture, origin, scale = image
    gray = picture.convert("L")
    forms, regions = settings.forms, settings.ingest.regions
    corner = raster.find_corner(gray, page.mm / scale, forms, regions)
    table = raster.find_table(gray, corner, forms, regions) if corner is not None else None
    if corner is None or table is None or table.other_form:
        return []
    turn = corner.turn

    def place(x: float, y: float) -> tuple[float, float]:
        ox, oy = turn.original(x, y)
        return origin[0] + ox * scale, origin[1] + oy * scale

    on_page = [r for r in rows if r.source is not None and r.source.page == page.number and r.source.bbox]
    vlm = settings.models.vlm
    margin = settings.models.ocr.cells.margin * table.s
    notes: list[str] = []
    for top, bottom in table.rows:
        band = page.norm_bbox(_rect(place, (table.left, top, table.left + 1, bottom)))
        row = next((r for r in on_page if r.source and r.source.bbox and _within(r.source.bbox, band)), None)
        if row is None:
            continue
        for field, label in vlm.rows.fields.items():
            left, right = table.columns[field]
            box = (table.left + left * table.s, top, table.left + right * table.s, bottom)
            crop = turn.crop(gray, (box[0] + margin, box[1] + margin, box[2] - margin, box[3] - margin))
            try:
                text = client.read(crop, label)
            except VlmError as exc:
                if exc.fatal:
                    raise
                notes.append(f"Ячейку «{label}» строки {_position(row)} модель не прочитала: {exc}")
                continue
            if not text:
                continue
            value = (eskd.normalize_designation(text) or text) if field == "designation" else text
            current: Extracted | None = getattr(row, field)
            if current is not None and _same(field, current.value, value, eskd):
                continue
            confidence = settings.ingest.confidence.vlm
            source = SourceRef(
                document_id=doc.id,
                page=page.number,
                bbox=page.norm_bbox(_rect(place, box)),
                text=text,
                method="vlm",
                confidence=confidence,
                engine=vlm.model,
                engine_version=client.version,
            )
            read = Extracted(value=value, raw=text, confidence=confidence, source=source)
            if current is None or current.value in (None, ""):
                setattr(row, field, read)
                continue
            note = f"Спецификация, строка {_position(row)}, «{label}»: прочитано «{current.value}», "
            note += f"модель прочла «{value}»"
            if vlm.conflict == "model":
                read.status = "conflict"
                setattr(row, field, read)
                notes.append(f"{note}; в отчёте — прочтение модели")
            else:
                current.status = "conflict"
                notes.append(note)
    return notes


def _rect(place: Place, box: BBox) -> BBox:
    """A box of the straightened image on the page: the bounds of its four corners."""
    corners = [place(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
    return (
        min(c[0] for c in corners),
        min(c[1] for c in corners),
        max(c[0] for c in corners),
        max(c[1] for c in corners),
    )


def _within(bbox: BBox, band: BBox) -> bool:
    """Whether the middle of ``bbox`` lies in the height of ``band``: the row OCR read in that band."""
    middle = (bbox[1] + bbox[3]) / 2
    return band[1] <= middle <= band[3]


def _position(row: SpecRow) -> str:
    return f"поз. {row.position.value}" if row.position is not None and row.position.value else row.id


def _same(field: str, a: object, b: object, eskd: Designations) -> bool:
    if field == "name":
        return eskd.normalize_name(str(a)) == eskd.normalize_name(str(b))
    return str(a) == str(b)
