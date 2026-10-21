"""Both PdfBackend implementations keep one contract: text, size and render share the displayed page (ADR-0007)."""

import sys
from importlib.util import find_spec
from pathlib import Path

import pytest
from kdcheck.ingest.document import load_document
from kdcheck.ingest.pdf import EncryptedPdfError, default_backend
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

# PyMuPDF (AGPL) is only for a rollback; without it the PDFium half of every test still runs.
needs_pymupdf = pytest.mark.skipif(find_spec("pymupdf") is None, reason="PyMuPDF is optional: kdcheck[pymupdf]")
BACKENDS = ["pdfium", pytest.param("pymupdf", marks=needs_pymupdf)]


def _canvas(path: Path, rotation: int = 0, password: str | None = None) -> Canvas:
    """A portrait 595×842 page turned by /Rotate. reportlab takes the displayed size, so a quarter turn swaps it."""
    canvas = Canvas(str(path), pagesize=(842, 595) if rotation % 180 else (595, 842), encrypt=password)
    canvas.setPageRotation(rotation)
    return canvas


def _text_pdf(path: Path, rotation: int = 0, password: str | None = None) -> Path:
    canvas = _canvas(path, rotation, password)
    canvas.drawString(100, 642, "ROT TEST")
    canvas.save()
    return path


def _scan_pdf(path: Path, size: tuple[int, int] = (1240, 1754), rotation: int = 0) -> Path:
    scan = Image.new("L", size, 255)
    scan.paste(0, (100, 100, 400, 160))
    canvas = _canvas(path, rotation)
    canvas.drawImage(ImageReader(scan), 0, 0, 595, 842)
    canvas.save()
    return path


def _ink(image: Image.Image) -> tuple[int, int, int, int]:
    box = image.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()
    assert box is not None
    return box


@pytest.mark.parametrize("name", BACKENDS)
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_text_sits_where_the_render_shows_it(tmp_path, name, rotation):
    page = default_backend(name).open(_text_pdf(tmp_path / "t.pdf", rotation).read_bytes()).page(0)
    portrait = rotation in (0, 180)
    assert (round(page.width), round(page.height)) == ((595, 842) if portrait else (842, 595))
    [line] = page.lines()
    assert line.text == "ROT TEST" and line.vertical is not portrait
    x0, y0, x1, y1 = _ink(page.render(1.0))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert line.bbox[0] <= cx <= line.bbox[2] and line.bbox[1] <= cy <= line.bbox[3]


@pytest.mark.parametrize("name", BACKENDS)
def test_password_protected_pdf(tmp_path, name):
    with pytest.raises(EncryptedPdfError, match="документ защищён паролем"):
        default_backend(name).open(_text_pdf(tmp_path / "s.pdf", password="x").read_bytes())


@pytest.mark.parametrize("name", BACKENDS)
def test_broken_pdf_is_damaged(tmp_path, name):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    doc = load_document(path, tmp_path, use_ocr=False, pdf_backend=default_backend(name))
    assert doc.info.status == "damaged" and doc.info.problem.startswith("Файл повреждён или не открывается")


def test_pdfium_reads_a_scan_at_its_own_pixels(tmp_path):
    page = default_backend("pdfium").open(_scan_pdf(tmp_path / "scan.pdf").read_bytes()).page(0)
    image, box = page.scan()
    assert image.size == (1240, 1754) and image.mode == "L"
    assert [round(v) for v in box] == [0, 0, 595, 842]
    assert _ink(image) == (100, 100, 400, 160)


def test_no_scan_on_a_text_page_or_a_rotated_page(tmp_path):
    pdfium = default_backend("pdfium")
    assert pdfium.open(_text_pdf(tmp_path / "t.pdf").read_bytes()).page(0).scan() is None
    assert pdfium.open(_scan_pdf(tmp_path / "rotated.pdf", rotation=90).read_bytes()).page(0).scan() is None


@needs_pymupdf
def test_pymupdf_renders_scans_as_before(tmp_path):
    """A rollback reproduces the old results: PyMuPDF renders a scan instead of reading its own pixels."""
    assert default_backend("pymupdf").open(_scan_pdf(tmp_path / "scan.pdf").read_bytes()).page(0).scan() is None


def test_missing_pymupdf_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymupdf", None)  # import pymupdf now fails as if it were not installed
    monkeypatch.delitem(sys.modules, "kdcheck.ingest.pymupdf_backend", raising=False)
    with pytest.raises(RuntimeError, match=r"install kdcheck\[pymupdf\]"):
        default_backend("pymupdf")


def test_unknown_backend_name():
    with pytest.raises(ValueError, match="pdfium, pymupdf"):
        default_backend("acrobat")
