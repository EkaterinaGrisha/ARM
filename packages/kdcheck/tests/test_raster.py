"""Regions of a page image by their ruled lines (kdcheck.layout.raster) and the cells read in them (kdcheck.ocr.cells).

The sheets are drawn here line by line, so the finder is checked without OCR and without the generator; reading the
cells needs Tesseract and is checked on the scans of the committed demo kit.
"""

import json
import math
from pathlib import Path

import pytest
from kdcheck.extract import specification, title_block
from kdcheck.ingest import document
from kdcheck.ingest.document import LoadedDocument, Page, load_document
from kdcheck.ingest.text import Line, Word
from kdcheck.layout import raster
from kdcheck.normalize.designations import Designations
from kdcheck.ocr import cells, tesseract
from kdcheck.run import load_settings
from kdcheck.schemas import DocumentInfo
from PIL import Image, ImageChops, ImageDraw

DPI = 200
PX = DPI / 25.4  # pixels per mm
KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"


def _sheet(settings, form: int | None, size=(420, 297), *, columns=(), angle=0.0, shift=(0.0, 0.0)) -> Image.Image:
    """A sheet with its frame and the main lines of a title block form (None: form 2a); with ``columns`` also the top
    of a specification table and its column lines. Then turned ``angle`` degrees and shifted ``shift`` mm."""
    w, h = size
    image = Image.new("L", (round(w * PX), round(h * PX)), 255)
    draw = ImageDraw.Draw(image)

    def line(x0: float, y0: float, x1: float, y1: float) -> None:
        draw.line([(x0 * PX, y0 * PX), (x1 * PX, y1 * PX)], fill=0, width=round(0.7 * PX))

    for x0, y0, x1, y1 in ((20, 5, w - 5, 5), (20, h - 5, w - 5, h - 5), (20, 5, 20, h - 5), (w - 5, 5, w - 5, h - 5)):
        line(x0, y0, x1, y1)
    template = settings.forms.title_block
    shape = template.forms[form] if form is not None else template.continuation
    x, y = w - 5 - template.width, h - 5 - shape.height
    for u0, v0, u1, v1 in shape.lines:
        line(x + u0, y + v0, x + u1, y + v1)
    if columns:
        header = 5 + settings.forms.specification.header_height
        line(x, header, x + template.width, header)
        for edge in columns:
            line(x + edge, 5, x + edge, y)
    if angle or shift != (0.0, 0.0):
        image = image.rotate(angle, translate=(shift[0] * PX, shift[1] * PX), fillcolor=255)
    return image


def _corner(found: raster.Corner) -> tuple[float, float]:
    """The top left corner of the block found, mm on the image."""
    x, y = found.turn.original(found.block.x0, found.block.y0)
    return x / PX, y / PX


@pytest.mark.parametrize(("form", "size"), [(1, (420, 297)), (2, (210, 297)), (None, (210, 297))])
def test_a_title_block_is_placed_by_the_lines_of_its_form(settings, form, size):
    found = raster.find_corner(_sheet(settings, form, size), PX, settings.forms, settings.ingest.regions)
    assert found is not None and found.block.form == form
    height = settings.forms.title_block.forms[form].height if form else settings.forms.title_block.continuation.height
    want = (size[0] - 5 - 185, size[1] - 5 - height)
    assert _corner(found) == pytest.approx(want, abs=0.5)
    assert found.block.s == pytest.approx(PX, rel=0.01)


def test_a_turned_sheet_is_read_straightened(settings):
    image = _sheet(settings, 1, angle=1.5)
    found = raster.find_corner(image, PX, settings.forms, settings.ingest.regions)
    assert found is not None and found.block.form == 1
    assert found.turn.angle == pytest.approx(-1.5, abs=0.1)
    # where the corner of the block went when the sheet turned about the centre of the image
    cx, cy = image.width / 2 / PX, image.height / 2 / PX
    a = math.radians(1.5)
    dx, dy = 420 - 5 - 185 - cx, 297 - 5 - 55 - cy
    want = (cx + dx * math.cos(a) + dy * math.sin(a), cy - dx * math.sin(a) + dy * math.cos(a))
    assert _corner(found) == pytest.approx(want, abs=0.5)


def test_a_sheet_shifted_off_the_glass_still_places_its_block(settings):
    """The right line of the frame and the right edge of the block are beyond the image."""
    found = raster.find_corner(_sheet(settings, 1, shift=(6, 0)), PX, settings.forms, settings.ingest.regions)
    assert found is not None and found.block.form == 1
    assert _corner(found) == pytest.approx((420 - 5 - 185 + 6, 297 - 5 - 55), abs=0.5)


def test_a_frame_without_a_title_block_places_none(settings):
    image = _sheet(settings, 1)
    blank = Image.new("L", image.size, 255)
    blank.paste(image.crop((0, 0, image.width, round(150 * PX))), (0, 0))  # the frame without its bottom part
    ImageDraw.Draw(blank).rectangle([(20 * PX, 5 * PX), (415 * PX, 292 * PX)], outline=0, width=round(0.7 * PX))
    assert raster.find_corner(blank, PX, settings.forms, settings.ingest.regions) is None


def test_specification_table_by_its_column_lines(settings):
    spec = settings.forms.specification
    edges = sorted({edge for pair in spec.columns.model_dump().values() for edge in pair} - {0.0, 185.0})
    image = _sheet(settings, 2, (210, 297), columns=edges)
    found = raster.find_corner(image, PX, settings.forms, settings.ingest.regions)
    table = raster.find_table(image, found, settings.forms, settings.ingest.regions)
    assert table is not None and not table.other_form
    top, bottom = 5 + spec.header_height, 297 - 5 - 40
    assert len(table.rows) == round((bottom - top) / spec.row_height)
    assert table.rows[0][0] / PX == pytest.approx(top, abs=0.5)
    other = raster.find_table(
        _sheet(settings, 2, (210, 297), columns=[*edges, *spec.other_columns]),
        found,
        settings.forms,
        settings.ingest.regions,
    )
    assert other is not None and other.other_form  # form 5 of GOST 2.113: its rows are not read as form 1


def test_a_drawing_has_no_specification_table(settings):
    image = _sheet(settings, 1)
    found = raster.find_corner(image, PX, settings.forms, settings.ingest.regions)
    assert raster.find_table(image, found, settings.forms, settings.ingest.regions) is None


def test_a_ruled_line_has_paper_beside_it(settings):
    """Grain or a dark area has ink along any line of a form: an empty textured page is not a title block."""
    rules = settings.ingest.regions
    s = rules.sample
    line = (10.0, 50.0, 300.0, 50.0)
    mask = Image.new("L", (320, 100), 0)
    draw = ImageDraw.Draw(mask)
    draw.line([(10, 50), (300, 50)], fill=raster.WHITE, width=3)
    assert raster._ruled(mask, line, s, rules)
    draw.rectangle([(0, 0), (320, 50)], fill=raster.WHITE)  # the edge of a dark area: paper on one side
    assert raster._ruled(mask, line, s, rules)
    draw.rectangle([(0, 0), (320, 100)], fill=raster.WHITE)  # ink everywhere
    assert not raster._ruled(mask, line, s, rules)
    assert not raster._ruled(Image.new("L", (320, 100), 0), line, s, rules)  # nothing there


def _grain(size: tuple[int, int]) -> Image.Image:
    """A page of grain as fine as a pixel: half of it dark, so each pixel of the copy of the ink is half full."""
    grain = Image.new("L", size, 255)
    grain.putdata([0 if (x + y) % 2 else 255 for y in range(size[1]) for x in range(size[0])])
    return grain


def test_a_form_is_found_under_grain(settings):
    sheet = _sheet(settings, 1)
    image = ImageChops.darker(sheet, _grain(sheet.size))
    found = raster.find_corner(image, PX, settings.forms, settings.ingest.regions)
    assert found is not None and found.block.form == 1
    assert _corner(found) == pytest.approx((420 - 5 - 185, 297 - 5 - 55), abs=0.5)


def test_grain_alone_places_no_block(settings):
    grain = _grain((round(420 * PX), round(297 * PX)))
    assert raster.find_corner(grain, PX, settings.forms, settings.ingest.regions) is None


def test_ink_density_goes_up_until_lines_lie_on_paper(settings):
    rules = settings.ingest.regions
    grain = _grain((400, 400))
    turn = raster.Turn(pivot=(200.0, 200.0), angle=0.0)
    box = (0.0, 0.0, 400.0, 400.0)
    only_lowest = rules.model_copy(update={"ink_density": rules.ink_density[:1]})
    assert raster.ink_share(raster.straight_ink(grain, turn, box, 127, 0.5, only_lowest)) > rules.max_mask_ink
    assert raster.ink_share(raster.straight_ink(grain, turn, box, 127, 0.5, rules)) <= rules.max_mask_ink


def test_each_step_of_the_search_stops_at_max_placements(settings, monkeypatch):
    rules = settings.ingest.regions.model_copy(update={"max_placements": 7})
    score, tried = raster._form_score, []

    def counted(*args):
        tried.append(args)
        return score(*args)

    monkeypatch.setattr(raster, "_form_score", counted)
    raster.find_corner(_sheet(settings, 1), PX, settings.forms, rules)
    assert 0 < len(tried) <= 7 * 4  # forms 1 and 2, then form 2a, each at the near scales and at any scale
    none = settings.ingest.regions.model_copy(update={"max_placements": 0})
    assert raster.find_corner(_sheet(settings, 1), PX, settings.forms, none) is None


def test_black_and_white_is_split_in_the_middle():
    """A turn leaves grey between the two levels, which a threshold at black would take for paper."""
    assert raster.otsu([100] + [0] * 254 + [900]) == 127
    assert 20 < raster.otsu([0] * 10 + [5, 9, 5] + [0] * 100 + [50, 90, 50] + [0] * 139) < 120


def test_placement_turns_cells_with_the_sheet():
    place = raster.Placement(100.0, 50.0, 2.0, angle=90.0)
    assert place.point(10, 0) == pytest.approx((100, 70))  # 10 mm along the block go down the page
    assert place.rect((0, 0, 10, 5)) == pytest.approx((90, 50, 100, 70))


def _reader(said: dict[str, str]):
    """A recognizer that says the text it was given for a set of characters, or «1»: where it read, and with what."""
    calls: list[str | None] = []

    def read(image, languages, page_segmentation, min_confidence, place, characters=None) -> list[Line]:
        calls.append(characters)
        x0, y0 = place(0, 0)
        x1, y1 = place(image.width, image.height)
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        text = said.get(characters or "", "1")
        return [Line(text, box, y1 - y0, words=[Word(text, box)], confidence=0.9)]

    read.calls = calls
    return read


def _ink(image: Image.Image, x: float, y: float) -> None:
    """A mark about a letter large at (x, y) mm on the sheet."""
    ImageDraw.Draw(image).rectangle([(x * PX, y * PX), ((x + 3) * PX, (y + 3) * PX)], fill=0)


def test_only_cells_with_ink_are_read_each_with_its_characters(settings, monkeypatch):
    image = _sheet(settings, 1)
    x, y = 420 - 5 - 185, 297 - 5 - 55
    _ink(image, x + 100, y + 6)  # the designation
    _ink(image, x + 2, y + 16)  # an entry of the change record table
    designation = settings.models.ocr.cells.characters["designation"]
    read = _reader({designation: "АБВГ.715211.001"})
    monkeypatch.setattr(tesseract, "words", read)
    regions = cells.find(image, 1.0, (0.0, 0.0), PX, settings)
    assert regions.block.form == 1 and regions.table is None
    assert read.calls == [designation, settings.models.ocr.cells.characters["changes"]]
    assert [ln.text for ln in regions.cells["designation"]] == ["АБВГ.715211.001"]
    assert regions.cells["name"] == [] and regions.cells["litera"] == []  # empty: not read
    x0, y0, x1, y1 = regions.cells["designation"][0].bbox  # the whole cell, with the paper added around it
    assert (x0 / PX, y0 / PX, x1 / PX, y1 / PX) == pytest.approx((x + 65.5, y + 0.5, x + 184.5, y + 14.5), abs=1.1)


def test_rows_of_a_table_come_by_column(settings, monkeypatch):
    spec = settings.forms.specification
    edges = sorted({edge for pair in spec.columns.model_dump().values() for edge in pair} - {0.0, 185.0})
    image = _sheet(settings, 2, (210, 297), columns=edges)
    top = 5 + spec.header_height
    _ink(image, 20 + 30, top + 2)  # the second cell is the name: two rows down
    _ink(image, 20 + 100, top + 2 + spec.row_height)
    monkeypatch.setattr(tesseract, "words", _reader({}))
    table = cells.find(image, 1.0, (0.0, 0.0), PX, settings).table
    assert table is not None and [list(row) for row in table.rows] == [["designation"], ["name"]]


def test_a_failure_of_the_regions_keeps_the_text_of_the_page(settings, monkeypatch):
    def fails(*args):
        raise ValueError("broken")

    line = Line("текст", (0, 0, 10, 10), 10)
    monkeypatch.setattr(tesseract, "recognize", lambda *args, **kwargs: [line])
    monkeypatch.setattr(tesseract, "version", lambda: "5")
    monkeypatch.setattr(cells, "find", fails)
    page = Page(number=1, width=100.0, height=100.0, unit="px", dpi=25.4)
    document._ocr(page, Image.new("L", (100, 100), 255), settings, 1.0, (0.0, 0.0))
    assert page.lines == [line] and page.method == "ocr" and page.regions is None


def _page_of_regions(regions: cells.Regions) -> LoadedDocument:
    page = Page(number=1, width=595.0, height=842.0, lines=[Line("текст", (0, 0, 10, 10), 10)], regions=regions)
    page.read_by("ocr", "tesseract", "5", 0.7)
    return LoadedDocument(DocumentInfo(id="d", file_name="a.tif", path="a.tif"), Path("a.tif"), pages=[page])


def _words(text: str) -> list[Word]:
    return [Word(text, (0, 0, 10, 10))]


def test_title_block_of_a_page_image_comes_from_its_cells(settings, eskd):
    def cell(text: str) -> list[Line]:
        return [Line(text, (100, 700, 200, 720), 20, words=_words(text), confidence=0.8)]

    block = cells.FoundBlock(1, raster.Placement(50.0, 680.0, 2.8, angle=1.0))
    doc = _page_of_regions(
        cells.Regions(block, cells={"designation": cell("АБВГ.715211.001"), "name": cell("Вал"), "changes": cell("3")})
    )
    page = doc.pages[0]
    geom = title_block.locate(page, settings.forms)
    assert (geom.form, geom.x0, geom.y0, geom.angle) == (1, 50.0, 680.0, 1.0)
    tb = title_block.parse(doc, page, geom, settings.forms, eskd)
    assert tb.designation.value == "АБВГ.715211.001" and tb.designation.confidence == 0.8
    assert tb.name.value == "Вал" and tb.name.source.method == "ocr"
    assert tb.revision is None  # the change number lies outside the entries of this block


def test_rows_read_by_column_make_positions_by_the_same_rules(settings, eskd):
    rows: list[cells.Row] = [
        {"name": _words("Детолу")},  # a section header misread
        {
            "position": _words("1"),
            "designation": _words("АБВГ.715211.001"),
            "name": _words("Вал"),
            "quantity": _words("2"),
        },
        {"name": _words("ведущий")},  # the name goes on
    ]
    block = cells.FoundBlock(2, raster.Placement(0.0, 700.0, 2.8))
    doc = _page_of_regions(cells.Regions(block, table=cells.FoundTable(rows=rows)))
    assert specification.is_specification(doc.pages[0], settings.forms)
    [row] = specification.parse(doc, settings.forms, eskd).rows
    assert (row.section, row.designation.value, row.name.value, row.quantity.value) == (
        "Детали",
        "АБВГ.715211.001",
        "Вал ведущий",
        2.0,
    )


def test_a_table_of_another_form_is_a_specification_without_rows(settings, eskd):
    doc = _page_of_regions(
        cells.Regions(cells.FoundBlock(2, raster.Placement(0.0, 700.0, 2.8)), table=cells.FoundTable(other_form=True))
    )
    assert specification.is_specification(doc.pages[0], settings.forms)
    assert specification.parse(doc, settings.forms, eskd).rows == []


OCR = load_settings(use_ocr=True)
needs_ocr = pytest.mark.skipif(not tesseract.available(OCR.models.ocr.languages), reason="нужен Tesseract")
TRUTH = json.loads((KIT.parent / "demo_kit_truth.json").read_text(encoding="utf-8"))["documents"]
SCANS = sorted(p for p, t in TRUTH.items() if t.get("origin") == "scan")


@needs_ocr
@pytest.mark.parametrize("path", SCANS)
def test_title_block_cells_of_a_scan_are_read_one_by_one(path):
    doc = load_document(KIT / path, KIT, OCR)
    page = doc.pages[0]
    assert page.regions is not None and page.regions.block.form == 1
    geom = title_block.locate(page, OCR.forms)
    tb = title_block.parse(doc, page, geom, OCR.forms, Designations(OCR.dictionaries))
    assert tb.designation is not None and tb.designation.value == TRUTH[path]["designation"]
    assert tb.designation.source.method == "ocr"
