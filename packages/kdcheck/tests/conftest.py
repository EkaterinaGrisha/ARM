"""Shared fixtures: the committed settings with OCR off, so results do not depend on Tesseract being installed."""

import pytest
from kdcheck.config import Settings, load_settings
from kdcheck.ingest.pdf import PdfBackend, PdfDocument, PdfPage, get_backend
from kdcheck.normalize.designations import Designations


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings(use_ocr=False)


@pytest.fixture(scope="session")
def eskd(settings: Settings) -> Designations:
    return Designations(settings.dictionaries)


class _OnePageFails:
    def __init__(self, doc: PdfDocument, broken: int) -> None:
        self._doc, self._broken = doc, broken

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def page(self, index: int) -> PdfPage:
        if index == self._broken:
            raise RuntimeError("Failed to load page.")
        return self._doc.page(index)


class BrokenPageBackend:
    """PDFium, except that one page fails to open, as a page with a broken object does."""

    def __init__(self, broken: int) -> None:
        self._pdfium = get_backend("pdfium")
        self.name = self._pdfium.name
        self._broken = broken

    def versions(self) -> dict[str, str]:
        return self._pdfium.versions()

    def open(self, data: bytes) -> PdfDocument:
        return _OnePageFails(self._pdfium.open(data), self._broken)


@pytest.fixture
def broken_page() -> type[PdfBackend]:
    """A PDF backend whose page at the given zero-based index fails: ``broken_page(1)``."""
    return BrokenPageBackend
