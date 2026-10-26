"""PdfBackend on PyMuPDF (AGPL-3.0), installed with ``kdcheck[pymupdf]``. Only this module imports it."""

from __future__ import annotations

import io
import math
from typing import Any

import pymupdf
from PIL import Image

from kdcheck.config import ScanRules, TextLayout
from kdcheck.ingest.pdf import EncryptedPdfError
from kdcheck.ingest.text import BBox, Line, Word, no_surrogates, split_wide_lines

pymupdf.TOOLS.mupdf_display_errors(False)


class PyMuPdfPage:
    def __init__(self, page: Any) -> None:
        self._page = page
        # Text comes in unrotated page space; this matrix moves it to the page as displayed and rendered.
        self._rotate = page.rotation_matrix if page.rotation else None

    @property
    def width(self) -> float:
        return float(self._page.rect.width)

    @property
    def height(self) -> float:
        return float(self._page.rect.height)

    def lines(self, layout: TextLayout) -> list[Line]:
        low, high = layout.vertical_angle
        lines: list[Line] = []
        for block in self._page.get_text("rawdict")["blocks"]:
            for ln in block.get("lines", []):
                words: list[Word] = []
                chars: list[tuple[str, BBox]] = []
                for span in ln["spans"]:
                    for ch in span["chars"]:
                        if ch["c"].isspace():
                            _flush_word(chars, words)
                        else:
                            chars.append((no_surrogates(ch["c"]), self._box(ch["bbox"])))
                _flush_word(chars, words)
                if not words:
                    continue
                size = max(span["size"] for span in ln["spans"])
                dx, dy = ln["dir"]
                if self._rotate is not None:  # the linear part only: a direction does not move
                    m = self._rotate
                    dx, dy = m.a * dx + m.c * dy, m.b * dx + m.d * dy
                vertical = low <= math.degrees(math.atan2(dy, dx)) % 180 <= high
                text = " ".join(w.text for w in words)
                lines.append(Line(text=text, bbox=self._box(ln["bbox"]), size=size, vertical=vertical, words=words))
        return split_wide_lines(lines, layout.cell_gap)

    def _box(self, values: Any) -> BBox:
        if self._rotate is None:
            return _bbox(values)
        r = pymupdf.Rect(values) * self._rotate
        return (r.x0, r.y0, r.x1, r.y1)

    def has_images(self) -> bool:
        return bool(self._page.get_images())

    def scan(self, rules: ScanRules) -> tuple[Image.Image, BBox] | None:
        return None  # kept for rollback: scans are rendered, as before the switch to PDFium

    def render(self, scale: float) -> Image.Image:
        pix = self._page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False)
        return Image.open(io.BytesIO(pix.tobytes("png")))


class PyMuPdfDocument:
    def __init__(self, doc: Any) -> None:
        self._doc = doc

    @property
    def page_count(self) -> int:
        return int(self._doc.page_count)

    def page(self, index: int) -> PyMuPdfPage:
        return PyMuPdfPage(self._doc[index])


class PyMuPdfBackend:
    name = "pymupdf"

    def versions(self) -> dict[str, str]:
        return {self.name: str(pymupdf.__version__), "mupdf": str(pymupdf.mupdf_version)}

    def open(self, data: bytes) -> PyMuPdfDocument:
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.needs_pass:
            raise EncryptedPdfError("документ защищён паролем")
        return PyMuPdfDocument(doc)


def _bbox(values: Any) -> BBox:
    x0, y0, x1, y1 = values
    return (x0, y0, x1, y1)


def _flush_word(chars: list[tuple[str, BBox]], words: list[Word]) -> None:
    if not chars:
        return
    text = "".join(c for c, _ in chars)
    boxes = [b for _, b in chars]
    words.append(
        Word(
            text,
            (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)),
        )
    )
    chars.clear()
