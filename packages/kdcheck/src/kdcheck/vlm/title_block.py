"""Title block cells read by a vision-language model (ADR-0014).

The GOST template of ``forms.yaml`` gives the rectangle of each cell. The model reads the text of that rectangle
only, and :func:`kdcheck.extract.title_block.cell_value` turns the text into the value by the same rules as OCR text,
so the cell is the evidence of the value. The model reads the cells the rules left empty, and with ``verify`` every
one: when its value differs from the one read before, that value stays, gets the ``conflict`` status, and the
model's reading goes to the notes of the document.
"""

from __future__ import annotations

from PIL import Image

from kdcheck.config import Settings
from kdcheck.extract.title_block import Geometry, cell_value
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.normalize.designations import Designations
from kdcheck.schemas import Extracted, SourceRef, TitleBlock
from kdcheck.vlm.client import VlmClient, VlmError

# The image the page was read from, where it sits on the page and page units per pixel, as ``page_raster`` gives.
Raster = tuple[Image.Image, tuple[float, float], float]


def read_cells(
    doc: LoadedDocument,
    page: Page,
    geom: Geometry,
    tb: TitleBlock,
    *,
    client: VlmClient,
    raster: Raster,
    settings: Settings,
    eskd: Designations,
) -> list[str]:
    """Read the cells of ``vlm.fields`` into ``tb``; return notes for the document.

    A reply the model gives in a wrong form costs one cell. A server that stops answering raises the fatal
    :class:`VlmError`, so the caller stops asking.
    """
    vlm = settings.models.vlm
    cells = settings.forms.title_block.forms[geom.form].cells
    labels = settings.forms.title_block.label_words
    notes: list[str] = []
    for field, label in vlm.fields.items():
        cell = getattr(cells, field)
        current: Extracted | None = getattr(tb, field)
        if cell is None or (current is not None and not vlm.verify):
            continue
        rect = geom.rect(cell)
        try:
            text = client.read(_crop(raster, rect), label)
        except VlmError as exc:
            if exc.fatal:
                raise
            notes.append(f"Ячейку «{label}» модель не прочитала: {exc}")
            continue
        value = cell_value(field, text, eskd, labels)
        if value is None:
            continue
        if current is None:
            confidence = settings.ingest.confidence.vlm
            source = SourceRef(
                document_id=doc.id,
                page=page.number,
                bbox=page.norm_bbox(rect),
                text=text,
                method="vlm",
                confidence=confidence,
                engine=vlm.model,
                engine_version=client.version,
            )
            setattr(tb, field, Extracted(value=value, raw=text, confidence=confidence, source=source))
        elif not _same(field, current.value, value, eskd):
            current.status = "conflict"
            notes.append(f"Основная надпись, «{label}»: прочитано «{current.value}», модель прочла «{value}»")
    return notes


def _crop(raster: Raster, rect: tuple[float, float, float, float]) -> Image.Image:
    image, (ox, oy), scale = raster
    x0, y0, x1, y1 = rect
    box = (
        max(0, round((x0 - ox) / scale)),
        max(0, round((y0 - oy) / scale)),
        min(image.width, round((x1 - ox) / scale)),
        min(image.height, round((y1 - oy) / scale)),
    )
    return image.crop(box)


def _same(field: str, a: object, b: object, eskd: Designations) -> bool:
    """Whether two values of a cell agree: both went through ``cell_value``, so only the writing may differ."""
    if field in ("name", "material"):
        return eskd.normalize_name(str(a)) == eskd.normalize_name(str(b))
    return str(a).replace(",", ".") == str(b).replace(",", ".")
