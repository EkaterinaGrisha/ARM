"""PDF access behind the ``PdfBackend`` interface (ADR-0007).

The core never imports a PDF library directly. PyMuPDF (AGPL-3.0) is the only backend for now and an optional
extra, ``kdcheck[pymupdf]``; pypdfium2 and pdfplumber replace it after the measurement in stage 6.
"""

from __future__ import annotations

from typing import Protocol

from PIL import Image

from kdcheck.ingest.text import Line


class EncryptedPdfError(ValueError):
    """The PDF needs a password."""


class PdfPage(Protocol):
    @property
    def width(self) -> float:
        """Page width in points."""
        ...

    @property
    def height(self) -> float:
        """Page height in points."""
        ...

    def lines(self) -> list[Line]:
        """Text layer as lines of words, coordinates in points from the top left corner."""
        ...

    def has_images(self) -> bool: ...

    def render(self, scale: float) -> Image.Image:
        """Grayscale raster of the page, ``scale`` pixels per point."""
        ...


class PdfDocument(Protocol):
    @property
    def page_count(self) -> int: ...

    def page(self, index: int) -> PdfPage:
        """Page by zero-based index."""
        ...


class PdfBackend(Protocol):
    name: str

    def open(self, data: bytes) -> PdfDocument:
        """Open a PDF from bytes; raises EncryptedPdfError for password-protected files."""
        ...


def default_backend() -> PdfBackend:
    try:
        from kdcheck.ingest.pymupdf_backend import PyMuPdfBackend
    except ImportError as exc:
        raise RuntimeError("No PDF backend installed: install kdcheck[pymupdf]") from exc
    return PyMuPdfBackend()
