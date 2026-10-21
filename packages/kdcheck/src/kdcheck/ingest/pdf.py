"""PDF access behind the ``PdfBackend`` interface (ADR-0007).

The core never imports a PDF library directly. ``ARM_PDF_BACKEND`` picks the implementation: ``pdfium``
(pypdfium2, ``kdcheck[pdfium]``) or ``pymupdf`` (AGPL-3.0, ``kdcheck[pymupdf]``, kept for rollback).
"""

from __future__ import annotations

import os
from typing import Protocol

from PIL import Image

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

    def lines(self) -> list[Line]:
        """Text layer as lines of words, in points from the top left corner of the page as displayed.

        On a rotated page (``/Rotate``) the coordinates follow the rotation, like ``width``, ``height`` and ``render``.
        """
        ...

    def has_images(self) -> bool: ...

    def scan(self) -> tuple[Image.Image, BBox] | None:
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

    def open(self, data: bytes) -> PdfDocument:
        """Open a PDF from bytes; raises EncryptedPdfError for password-protected files."""
        ...


BACKENDS = ("pdfium", "pymupdf")
DEFAULT_BACKEND = "pymupdf"


def default_backend(name: str | None = None) -> PdfBackend:
    """The backend named by ``name``, else by ``ARM_PDF_BACKEND``, else the default one."""
    name = name or os.environ.get("ARM_PDF_BACKEND") or DEFAULT_BACKEND
    try:
        if name == "pdfium":
            from kdcheck.ingest.pdfium_backend import PdfiumBackend

            return PdfiumBackend()
        if name == "pymupdf":
            from kdcheck.ingest.pymupdf_backend import PyMuPdfBackend

            return PyMuPdfBackend()
    except ImportError as exc:
        raise RuntimeError(f"PDF backend {name} is not installed: install kdcheck[{name}]") from exc
    raise ValueError(f"Unknown PDF backend {name!r}: expected one of {', '.join(BACKENDS)}")
