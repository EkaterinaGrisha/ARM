"""Blank sheets (plan item 4.1): a page of nothing but paper is no unreadable scan; a file of such pages is blank."""

import io
import random

import pypdfium2 as pdfium
from kdcheck.ingest import document
from kdcheck.run import BaselinePipeline
from PIL import Image, ImageDraw, ImageFilter

A4 = (1240, 1754)  # px at 150 dpi
PPMM = 150 / 25.4


def _paper(seed: int = 1, level: int = 236) -> Image.Image:
    """Paper of an old album: grain, a few specks, a stain and the shadow of a fold."""
    rng = random.Random(seed)
    grain = Image.frombytes("L", A4, rng.randbytes(A4[0] * A4[1])).point(lambda v: level - 6 + v * 12 // 255)
    draw = ImageDraw.Draw(grain)
    for _ in range(12):
        x, y = rng.randrange(A4[0]), rng.randrange(A4[1])
        draw.ellipse((x, y, x + 2, y + 2), fill=level - 60)
    draw.ellipse((300, 500, 700, 800), fill=level - 40)  # a stain
    draw.rectangle((0, 900, A4[0], 1300), fill=level - 25)  # the shadow of a fold
    for y in (300, 900, 1500):  # binding holes at the edge
        draw.ellipse((A4[0] - 140, y, A4[0] - 60, y + 80), fill=40)
    return grain.filter(ImageFilter.GaussianBlur(1.2))


def _drawing(image: Image.Image, ink: int) -> Image.Image:
    """``image`` with a frame, a title block and a few lines drawn ``ink`` grey levels below the paper."""
    out = image.copy()
    draw = ImageDraw.Draw(out)
    level = lambda xy: max(0, out.getpixel(xy) - ink)  # noqa: E731
    draw.rectangle((118, 30, A4[0] - 30, A4[1] - 30), outline=level((118, 30)), width=3)
    draw.rectangle((A4[0] - 1000, A4[1] - 360, A4[0] - 30, A4[1] - 30), outline=level((240, 1400)), width=2)
    for y in range(400, 1200, 160):
        draw.line((300, y, 900, y), fill=level((300, y)), width=2)
    return out


def test_paper_with_stains_holes_and_specks_is_blank(settings):
    rules = settings.ingest.blank
    assert document.is_blank(Image.new("L", A4, 255), PPMM, rules)
    assert document.is_blank(_paper(), PPMM, rules)
    with_number = _paper(2)
    ImageDraw.Draw(with_number).text((A4[0] // 2, A4[1] - 140), "22", fill=60)  # a page number of an album
    assert document.is_blank(with_number, PPMM, rules)


def test_a_faint_drawing_is_not_blank(settings):
    rules = settings.ingest.blank
    a1 = _drawing(Image.new("L", A4, 255), 200).resize((A4[0] // 2, A4[1] // 2))  # the same lines on a sheet 4 times
    assert not document.is_blank(a1, PPMM / 4, rules)  # as large: the cells are millimetres, not pixels
    assert not document.is_blank(_drawing(Image.new("L", A4, 255), 200), PPMM, rules)
    assert not document.is_blank(_drawing(_paper(3), 30), PPMM, rules)  # pencil on a tracing


def test_a_page_smaller_than_the_margins_is_measured_whole(settings):
    rules = settings.ingest.blank
    assert document.is_blank(Image.new("L", (12, 12), 255), 8.5, rules)  # a 4 pt placeholder page of an archive
    tiny = Image.new("L", (60, 60), 255)
    ImageDraw.Draw(tiny).rectangle((10, 10, 50, 50), outline=0, width=2)
    assert not document.is_blank(tiny, 3.0, rules)


def test_a_dark_image_is_left_to_the_quality_of_the_scan(settings):
    assert not document.is_blank(Image.new("L", A4, 30), PPMM, settings.ingest.blank)


def _tiff(path, pages):
    buf = io.BytesIO()
    pages[0].save(buf, "TIFF", save_all=True, append_images=pages[1:], dpi=(150, 150))
    path.write_bytes(buf.getvalue())
    return path


def test_a_blank_page_among_others_is_a_note_and_not_an_error(tmp_path, settings):
    sheet = _drawing(Image.new("L", A4, 250), 200)
    path = _tiff(tmp_path / "Деталь.tif", [_paper(), sheet, _paper(4)])
    doc = document.load_document(path, tmp_path, settings)
    assert doc.info.status == "ok"
    assert [p.blank for p in doc.pages] == [True, False, True]
    assert [p.problem for p in doc.info.pages] == [document.BLANK, None, document.BLANK]
    assert doc.quality and doc.unreadable is None  # measured on the drawing, not on the blank first page
    report = BaselinePipeline(settings).run(tmp_path, kit_id="k", kit_name="k")
    (info,) = report.documents
    assert "Пустые листы 1, 3 из 3: ни текста, ни линий, не распознаются" in info.notes
    assert not any("Страница 1" in e.message for e in report.log)  # no error for a blank page


def test_a_file_of_blank_pages_is_blank(tmp_path, settings):
    _tiff(tmp_path / "скан_0035.tif", [_paper(), _paper(5)])
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(595, 842)
    buf = io.BytesIO()
    pdf.save(buf)
    (tmp_path / "Лист.pdf").write_bytes(buf.getvalue())
    report = BaselinePipeline(settings).run(tmp_path, kit_id="k", kit_name="k")
    docs = {d.file_name: d for d in report.documents}
    scan, vector = docs["скан_0035.tif"], docs["Лист.pdf"]
    assert (scan.status, scan.origin) == ("blank", "scan")
    assert scan.problem == "Пустые листы: ни на одном из 2 нет ни текста, ни линий"
    assert (vector.status, vector.origin) == ("blank", "vector")
    assert vector.problem == "Пустой лист: на нём нет ни текста, ни линий"
    assert report.summary.documents_by_status == {"blank": 2}
    assert {e.document_id for e in report.log if e.level == "warning"} >= {scan.id, vector.id}


def test_an_unreadable_scan_stays_unreadable(tmp_path, settings):
    # as the unreadable class of the generator: grey paper, faint ink, blurred past reading
    faint = _drawing(Image.new("L", A4, 140), 30).filter(ImageFilter.GaussianBlur(2))
    doc = document.load_document(_tiff(tmp_path / "скан.tif", [faint]), tmp_path, settings)
    assert not doc.pages[0].blank
    assert doc.unreadable
