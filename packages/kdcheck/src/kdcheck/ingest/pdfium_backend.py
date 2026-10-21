"""PdfBackend on pypdfium2 (BSD-3-Clause or Apache-2.0; PDFium itself is BSD-3-Clause), the default one.

PDFium reports characters in unrotated PDF user space. They are moved to the page as displayed, the same space as
the page size and the rendered image, so title block geometry and source highlights work on rotated sheets too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageChops, ImageStat

from kdcheck.ingest.pdf import EncryptedPdfError
from kdcheck.ingest.text import BBox, Line, Word, split_wide_lines

GAP = 1.6  # a gap wider than this many font sizes along the text ends the line: the next cell starts
SCAN_COVERAGE = 0.5  # a scan covers at least this share of the page


@dataclass
class _Char:
    text: str
    box: BBox
    size: float
    vertical: bool
    generated: bool


class PdfiumPage:
    def __init__(self, page: pdfium.PdfPage) -> None:
        self._page = page
        self._rotation = page.get_rotation()
        self._crop = page.get_cropbox()

    @property
    def width(self) -> float:
        return float(self._page.get_width())

    @property
    def height(self) -> float:
        return float(self._page.get_height())

    def _display(self, left: float, bottom: float, right: float, top: float) -> BBox:
        """PDF user space box → displayed page box, origin top left, y down."""
        cl, cb, cr, ct = self._crop
        w0, h0 = cr - cl, ct - cb
        corners = [(x - cl, ct - y) for x, y in ((left, bottom), (right, top))]
        if self._rotation == 90:
            corners = [(h0 - y, x) for x, y in corners]
        elif self._rotation == 180:
            corners = [(w0 - x, h0 - y) for x, y in corners]
        elif self._rotation == 270:
            corners = [(y, w0 - x) for x, y in corners]
        (ax, ay), (bx, by) = corners
        return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

    def _chars(self) -> list[_Char]:
        textpage = self._page.get_textpage()
        chars: list[_Char] = []
        for i in range(textpage.count_chars()):
            code = pdfium_c.FPDFText_GetUnicode(textpage, i)
            angle = pdfium_c.FPDFText_GetCharAngle(textpage, i)
            displayed = (math.degrees(angle) + self._rotation) % 180 if angle >= 0 else 0.0
            chars.append(
                _Char(
                    text=chr(code) if code else "",
                    box=self._display(*textpage.get_charbox(i, loose=True)),
                    size=float(pdfium_c.FPDFText_GetFontSize(textpage, i)),
                    vertical=60 <= displayed <= 120,
                    generated=bool(pdfium_c.FPDFText_IsGenerated(textpage, i)),
                )
            )
        return chars

    def lines(self) -> list[Line]:
        lines: list[Line] = []
        run: list[_Char] = []
        for ch in self._chars():
            if ch.text in ("\r", "\n"):
                _flush(run, lines)
                continue
            if ch.generated and ch.text.isspace():
                _flush(run, lines)  # PDFium marks the boundary of a text object, here a table cell, with a space
                continue
            if run and _breaks(run, ch):
                _flush(run, lines)
            run.append(ch)
        _flush(run, lines)
        return split_wide_lines(lines)

    def has_images(self) -> bool:
        return any(True for _ in self._page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=2))

    def scan(self) -> tuple[Image.Image, BBox] | None:
        if self._rotation:  # the pixels would need turning first
            return None
        best: tuple[float, pdfium.PdfImage, tuple[float, float, float, float]] | None = None
        for obj in self._page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=2):
            left, bottom, right, top = obj.get_bounds()
            area = (right - left) * (top - bottom)
            if best is None or area > best[0]:
                best = (area, obj, (left, bottom, right, top))
        if best is None or best[0] < SCAN_COVERAGE * self.width * self.height:
            return None
        _, obj, (left, bottom, right, top) = best
        a, b, c, d, _, _ = obj.get_matrix().get()
        if abs(b) > 1e-6 or abs(c) > 1e-6 or a <= 0 or d <= 0:  # rotated, skewed or mirrored
            return None
        image: Image.Image = obj.get_bitmap(render=False).to_pil().convert("L")
        sx, sy = (right - left) / image.width, (top - bottom) / image.height
        if abs(sx - sy) > 0.02 * sx:  # stretched
            return None
        box = self._display(left, bottom, right, top)
        if not self._looks_like(image, box):
            return None
        return image, box

    def _looks_like(self, image: Image.Image, box: BBox) -> bool:
        """Guard against masks and transparency: the scan must look like the rendered page in a thumbnail."""
        scale = 120 / max(self.width, self.height)
        x0, y0, x1, y1 = (round(v * scale) for v in box)
        page = self.render(scale).crop((x0, y0, x1, y1))
        if page.width < 4 or page.height < 4:
            return False
        thumb = image.resize(page.size)
        diff = ImageStat.Stat(ImageChops.difference(page, thumb)).mean[0]
        return diff < 25

    def render(self, scale: float) -> Image.Image:
        image: Image.Image = self._page.render(scale=scale, grayscale=True).to_pil()
        return image


def _breaks(run: list[_Char], ch: _Char) -> bool:
    """Whether ``ch`` cannot continue the line in ``run``: other direction, other baseline or a cell gap."""
    last = next(c for c in reversed(run) if not c.generated)
    if ch.vertical != last.vertical:
        return True
    size = max(last.size, ch.size, 1.0)
    # Text may run either way along its axis: up or down when vertical, right to left on a sheet turned 180°.
    axis = 1 if last.vertical else 0
    across = abs((ch.box[1 - axis] + ch.box[3 - axis]) - (last.box[1 - axis] + last.box[3 - axis])) / 2
    if ch.box[axis] >= last.box[axis]:
        along = ch.box[axis] - last.box[axis + 2]
    else:
        along = last.box[axis] - ch.box[axis + 2]
    return across > 0.5 * size or along > GAP * size


def _flush(run: list[_Char], lines: list[Line]) -> None:
    """Close the line in ``run``: words split at spaces, real or marked by PDFium."""
    words: list[Word] = []
    current: list[_Char] = []
    for ch in run:
        if ch.text.strip():
            current.append(ch)
        elif current:
            words.append(_word(current))
            current = []
    if current:
        words.append(_word(current))
    real = [c for c in run if not c.generated and c.text.strip()]
    if words:
        lines.append(
            Line(
                text=" ".join(w.text for w in words),
                bbox=_union([c.box for c in real]),
                size=max(c.size for c in real),
                vertical=real[0].vertical,
                words=words,
            )
        )
    run.clear()


def _word(chars: list[_Char]) -> Word:
    return Word("".join(c.text for c in chars), _union([c.box for c in chars]))


def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


class PdfiumDocument:
    def __init__(self, doc: pdfium.PdfDocument) -> None:
        self._doc = doc

    @property
    def page_count(self) -> int:
        return len(self._doc)

    def page(self, index: int) -> PdfiumPage:
        return PdfiumPage(self._doc[index])


class PdfiumBackend:
    name = "pdfium"

    def open(self, data: bytes) -> PdfiumDocument:
        try:
            doc = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD:
                raise EncryptedPdfError("документ защищён паролем") from exc
            raise
        return PdfiumDocument(doc)
