"""PDF access behind the ``PdfBackend`` interface (ADR-0007).

The core never imports a PDF library directly. ``pdf.backend`` in ``models.yaml`` picks the implementation, and
``ARM_PDF_BACKEND`` overrides it: ``pdfium`` (pypdfium2, installed with the core) or ``pymupdf`` (AGPL-3.0, only for
a rollback, ``kdcheck[pymupdf]``).
"""

from __future__ import annotations

from typing import Protocol

from PIL import Image

from kdcheck.config import ScanRules, TextLayout
from kdcheck.ingest.text import BBox, Line


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

    def lines(self, layout: TextLayout) -> list[Line]:
        """Text layer as lines of words, in points from the top left corner of the page as displayed.

        On a rotated page (``/Rotate``) the coordinates follow the rotation, like ``width``, ``height`` and ``render``.
        """
        ...

    def has_images(self) -> bool: ...

    def scan(self, rules: ScanRules) -> tuple[Image.Image, BBox] | None:
        """The page's scan at its own pixels, grayscale, and its box on the displayed page.

        None when the page is not one upright image covering most of it: then the page is rendered instead.
        OCR then reads the pixels as scanned, without the blur of resampling.
        """
        ...

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

    def versions(self) -> dict[str, str]:
        """Versions of the engine that reads the text, under ``name``, and of the libraries around it."""
        ...

    def open(self, data: bytes) -> PdfDocument:
        """Open a PDF from bytes; raises EncryptedPdfError for password-protected files."""
        ...


BACKENDS = ("pdfium", "pymupdf")


def get_backend(name: str) -> PdfBackend:
    """The backend named ``name``: ``pdfium`` or ``pymupdf``."""
    if name == "pdfium":
        from kdcheck.ingest.pdfium_backend import PdfiumBackend

        return PdfiumBackend()
    if name == "pymupdf":
        try:
            from kdcheck.ingest.pymupdf_backend import PyMuPdfBackend
        except ImportError as exc:
            raise RuntimeError("PDF backend pymupdf is not installed: install kdcheck[pymupdf]") from exc
        return PyMuPdfBackend()
    raise ValueError(f"Unknown PDF backend {name!r}: expected one of {', '.join(BACKENDS)}")
