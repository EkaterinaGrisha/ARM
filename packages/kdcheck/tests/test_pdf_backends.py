"""Both PdfBackend implementations keep one contract: text, size and render share the displayed page (ADR-0007)."""

import sys
from importlib.util import find_spec
from pathlib import Path

import pytest
from kdcheck.ingest.document import load_document
from kdcheck.ingest.pdf import EncryptedPdfError, get_backend
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


def _stream(data: bytes, entries: bytes = b"") -> bytes:
    return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (entries, len(data), data)


def _font_pdf(path: Path, codes: str, font: list[bytes]) -> Path:
    """A page showing the character codes ``codes`` (hex) in the font of objects 5 onwards.

    Written by hand: reportlab cannot make the fonts other PDF writers make.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        _stream(b"BT /F1 12 Tf 72 700 Td <%s> Tj ET" % codes.encode()),
        *font,
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(out)
    return path


# The UTF-8 CMap of the Haru library cut to ASCII, Cyrillic and «№»; its CIDs are the code points.
HARU_CMAP = b"""/CIDInit /ProcSet findresource begin 12 dict begin begincmap
/CIDSystemInfo << /Registry (Haru) /Ordering (Modern1) /Supplement 0 >> def
/CMapName /Modern-UTF8-H def /CMapType 1 def
4 begincodespacerange <00> <7F> <C280> <DFBF> <E0A080> <E0BFBF> <E18080> <EFBFBF> endcodespacerange
4 begincidrange <00> <7F> 0 <D080> <D0BF> 1024 <D180> <D1BF> 1088 <E28480> <E284BF> 8448 endcidrange
endcmap CMapName currentdict /CMap defineresource pop end end"""
HARU_INFO = b"/CIDSystemInfo << /Registry (Haru) /Ordering (Modern1) /Supplement 0 >>"


def _haru_font() -> list[bytes]:
    """A Type0 font as KOMPAS-3D writes it through Haru: the UTF-8 CMap is its encoding and its ToUnicode map."""
    return [
        b"<< /Type /Font /Subtype /Type0 /BaseFont /GOSTTypeA /Encoding 6 0 R /ToUnicode 6 0 R"
        b" /DescendantFonts [7 0 R] >>",
        _stream(HARU_CMAP, b"/Type /CMap /CMapName /Modern-UTF8-H " + HARU_INFO),
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /GOSTTypeA %s /FontDescriptor 8 0 R /DW 500 >>" % HARU_INFO,
        b"<< /Type /FontDescriptor /FontName /GOSTTypeA /Flags 32 /FontBBox [0 -200 1000 800] /ItalicAngle 0"
        b" /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>",
    ]


def _unicode_font(*targets: str) -> list[bytes]:
    """Helvetica whose ToUnicode map gives the codes 01, 02… the UTF-16 ``targets``."""
    pairs = b" ".join(b"<%02X> <%s>" % (n, target.encode()) for n, target in enumerate(targets, start=1))
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap"
        b" /CMapName /Adobe-Identity-UCS def /CMapType 2 def"
        b" 1 begincodespacerange <00> <FF> endcodespacerange %d beginbfchar %s endbfchar"
        b" endcmap CMapName currentdict /CMap defineresource pop end end" % (len(targets), pairs)
    )
    return [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>", _stream(cmap)]


def _ink(image: Image.Image) -> tuple[int, int, int, int]:
    box = image.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()
    assert box is not None
    return box


@pytest.mark.parametrize("name", BACKENDS)
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_text_sits_where_the_render_shows_it(tmp_path, settings, name, rotation):
    page = get_backend(name).open(_text_pdf(tmp_path / "t.pdf", rotation).read_bytes()).page(0)
    portrait = rotation in (0, 180)
    assert (round(page.width), round(page.height)) == ((595, 842) if portrait else (842, 595))
    [line] = page.lines(settings.ingest.text)
    assert line.text == "ROT TEST" and line.vertical is not portrait
    x0, y0, x1, y1 = _ink(page.render(1.0))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert line.bbox[0] <= cx <= line.bbox[2] and line.bbox[1] <= cy <= line.bbox[3]


@pytest.mark.parametrize("name", BACKENDS)
def test_password_protected_pdf(tmp_path, name):
    with pytest.raises(EncryptedPdfError, match="документ защищён паролем"):
        get_backend(name).open(_text_pdf(tmp_path / "s.pdf", password="x").read_bytes())


@pytest.mark.parametrize("name", BACKENDS)
def test_broken_pdf_is_damaged(tmp_path, settings, name):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    doc = load_document(path, tmp_path, settings, pdf_backend=get_backend(name))
    assert doc.info.status == "damaged" and doc.info.problem.startswith("Файл повреждён или не открывается")


@pytest.mark.parametrize("name", BACKENDS)
def test_kompas_export_reads_as_text(tmp_path, settings, name):
    """PDFium reported this text as Hangul syllables and a code beyond Unicode, and the page was lost."""
    text = "Инв. № подл."
    pdf = _font_pdf(tmp_path / "kompas.pdf", text.encode().hex(), _haru_font())
    [line] = get_backend(name).open(pdf.read_bytes()).page(0).lines(settings.ingest.text)
    assert line.text == text


@pytest.mark.parametrize("name", BACKENDS)
def test_korean_text_stays_korean(tmp_path, settings, name):
    """킞 is D0 9E, the UTF-8 bytes of «О»; next to 가, which is no UTF-8, the page is real Korean text."""
    pdf = _font_pdf(tmp_path / "ko.pdf", "0102", _unicode_font("D09E", "AC00"))
    [line] = get_backend(name).open(pdf.read_bytes()).page(0).lines(settings.ingest.text)
    assert line.text == "킞가"


@pytest.mark.parametrize("name", BACKENDS)
def test_lone_surrogate_becomes_a_replacement_character(tmp_path, settings, name):
    pdf = _font_pdf(tmp_path / "surrogate.pdf", "010203", _unicode_font("0041", "D800", "0042"))
    [line] = get_backend(name).open(pdf.read_bytes()).page(0).lines(settings.ingest.text)
    assert line.text == "A\ufffdB"


def test_a_code_that_is_no_character_does_not_cost_the_page(tmp_path, settings):
    """ED A0 80 encodes a surrogate, which UTF-8 forbids; as a code it lies beyond Unicode and broke chr()."""
    pdf = _font_pdf(tmp_path / "bad.pdf", "41EDA08042", _haru_font())
    doc = load_document(pdf, tmp_path, settings, pdf_backend=get_backend("pdfium"))
    assert doc.info.status != "damaged"
    assert [ln.text for ln in doc.pages[0].lines] == ["A\ufffdB"]


def test_pdfium_reads_a_scan_at_its_own_pixels(tmp_path, settings):
    page = get_backend("pdfium").open(_scan_pdf(tmp_path / "scan.pdf").read_bytes()).page(0)
    image, box = page.scan(settings.ingest.scan)
    assert image.size == (1240, 1754) and image.mode == "L"
    assert [round(v) for v in box] == [0, 0, 595, 842]
    assert _ink(image) == (100, 100, 400, 160)


def test_no_scan_on_a_text_page_or_a_rotated_page(tmp_path, settings):
    pdfium, rules = get_backend("pdfium"), settings.ingest.scan
    assert pdfium.open(_text_pdf(tmp_path / "t.pdf").read_bytes()).page(0).scan(rules) is None
    assert pdfium.open(_scan_pdf(tmp_path / "rotated.pdf", rotation=90).read_bytes()).page(0).scan(rules) is None


@needs_pymupdf
def test_pymupdf_renders_scans_as_before(tmp_path, settings):
    """A rollback reproduces the old results: PyMuPDF renders a scan instead of reading its own pixels."""
    page = get_backend("pymupdf").open(_scan_pdf(tmp_path / "scan.pdf").read_bytes()).page(0)
    assert page.scan(settings.ingest.scan) is None


def test_missing_pymupdf_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymupdf", None)  # import pymupdf now fails as if it were not installed
    monkeypatch.delitem(sys.modules, "kdcheck.ingest.pymupdf_backend", raising=False)
    with pytest.raises(RuntimeError, match=r"install kdcheck\[pymupdf\]"):
        get_backend("pymupdf")


def test_unknown_backend_name():
    with pytest.raises(ValueError, match="pdfium, pymupdf"):
        get_backend("acrobat")
