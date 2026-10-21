"""Both PdfBackend implementations keep one contract: text, size and render share the displayed page (ADR-0007)."""

import io
from pathlib import Path

import pytest
from kdcheck.ingest.document import load_document
from kdcheck.ingest.pdf import EncryptedPdfError, default_backend
from PIL import Image

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("pypdfium2")

BACKENDS = ["pdfium", "pymupdf"]


def _text_pdf(path: Path, rotation: int = 0, password: str | None = None) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 200), "ROT TEST", fontname="helv", fontsize=12)
    page.set_rotation(rotation)
    options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password} if password else {}
    doc.save(path, **options)
    return path


def _scan_pdf(path: Path, size: tuple[int, int] = (1240, 1754)) -> Path:
    scan = Image.new("L", size, 255)
    scan.paste(0, (100, 100, 400, 160))
    buf = io.BytesIO()
    scan.save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
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
    rotated = _scan_pdf(tmp_path / "scan.pdf")
    doc = pymupdf.open(rotated)
    doc[0].set_rotation(90)
    doc.save(tmp_path / "rotated.pdf")
    assert pdfium.open((tmp_path / "rotated.pdf").read_bytes()).page(0).scan() is None
    assert default_backend("pymupdf").open(rotated.read_bytes()).page(0).scan() is None


def test_unknown_backend_name():
    with pytest.raises(ValueError, match="pdfium, pymupdf"):
        default_backend("acrobat")
