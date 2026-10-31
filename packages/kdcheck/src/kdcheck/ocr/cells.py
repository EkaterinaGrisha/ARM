"""Regions of a page image found by their ruled lines (:mod:`kdcheck.layout.raster`), and their cells read one by one.

The title block of a raster or a scan is placed by the lines of its form, so its cells are known even when the
recognizer misses the column labels; the table of a specification is placed by the frame and the block under it, its
columns and rows by GOST 2.106. Each cell of the block and each cell of a row is cut from the straightened image inside
its ruled lines, the lines that still cross it are wiped, and an empty one is not read. The rest is brought to the text
height the recognizer reads best and recognized in the languages of the cells. Text goes to the page in page units,
where the title block and the specification parsers take it from (:mod:`kdcheck.extract`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageOps

from kdcheck.config import RegionRules, Settings
from kdcheck.ingest.text import Line, Word
from kdcheck.layout import raster
from kdcheck.ocr import tesseract

Place = Callable[[float, float], tuple[float, float]]  # a pixel of the straightened image → a page point
Row = dict[str, list[Word]]  # the words of a specification row by column


@dataclass(frozen=True)
class FoundBlock:
    """A title block found on a page image: its form (None for form 2a of a later sheet) and where it sits."""

    form: int | None
    placement: raster.Placement


@dataclass
class FoundTable:
    """The table of a specification found on a page image: its rows read, none for a table of another form."""

    rows: list[Row] = field(default_factory=list)
    other_form: bool = False


@dataclass
class Regions:
    block: FoundBlock
    cells: dict[str, list[Line]] = field(default_factory=dict)  # title block cells read one by one, by field
    table: FoundTable | None = None


def find(
    image: Image.Image, scale: float, origin: tuple[float, float], ppmm: float, settings: Settings
) -> Regions | None:
    """The regions of a page image placed on a page, with the cells of the title block and the rows of the table
    read: page = ``origin`` + pixels × ``scale``; ``ppmm`` is pixels per mm of the image."""
    gray = image.convert("L")
    forms = settings.forms
    corner = raster.find_corner(gray, ppmm, forms, settings.ingest.regions)
    if corner is None:
        return None
    block, turn = corner.block, corner.turn

    def place(x: float, y: float) -> tuple[float, float]:
        """A pixel of the straightened image on the page."""
        ox, oy = turn.original(x, y)
        return origin[0] + ox * scale, origin[1] + oy * scale

    x0, y0 = place(block.x0, block.y0)
    found = Regions(FoundBlock(block.form, raster.Placement(x0, y0, block.s * scale, turn.angle)))
    if block.form in forms.title_block.forms:
        for name, (u0, v0, u1, v1) in forms.title_block.forms[block.form].cells.model_dump(exclude_none=True).items():
            box = (block.x0 + u0 * block.s, block.y0 + v0 * block.s, block.x0 + u1 * block.s, block.y0 + v1 * block.s)
            found.cells[name] = read_cell(gray, corner, box, settings, place, name)
    table = raster.find_table(gray, corner, forms, settings.ingest.regions)
    if table is not None:
        found.table = FoundTable(other_form=table.other_form)
        if not table.other_form:
            found.table.rows = [
                row for row in (_read_row(gray, corner, table, r, settings, place) for r in table.rows) if row
            ]
    return found


def _read_row(
    gray: Image.Image,
    corner: raster.Corner,
    table: raster.Table,
    row: tuple[float, float],
    settings: Settings,
    place: Place,
) -> Row:
    """The words of a specification row, column by column; none for an empty row."""
    top, bottom = row
    words: Row = {}
    for name, (left, right) in table.columns.items():
        box = (table.left + left * table.s, top, table.left + right * table.s, bottom)
        if found := [w for line in read_cell(gray, corner, box, settings, place, name) for w in line.words]:
            words[name] = found
    return words


def read_cell(
    gray: Image.Image, corner: raster.Corner, box: raster.Box, settings: Settings, place: Place, field: str
) -> list[Line]:
    """The lines of the cell ``field`` at ``box`` of the straightened image; none when it holds less ink than a digit.

    The cell is cut inside its ruled lines, the lines across it are wiped, and its tallest line of text is brought to
    the height the recognizer reads best, in black on white.
    """
    cells, s, level = settings.models.ocr.cells, corner.block.s, corner.level
    m = cells.margin * s
    x0, y0, x1, y1 = box[0] + m, box[1] + m, box[2] - m, box[3] - m
    image = wipe_lines(corner.turn.crop(gray, (x0, y0, x1, y1)), level, s, settings.ingest.regions)
    ink = raster.ink_mask(image, level)
    edge = settings.ingest.regions.line_band * s  # the edge of a ruled line may still lie along the cell: not text
    middle = raster.crop(ink, (edge, edge, ink.width - edge, ink.height - edge), fill=0)
    if raster.ink_share(middle) * middle.width * middle.height / s**2 < cells.min_ink:
        return []
    low, high = cells.text_range
    heights = [h for h in _bands(raster.profile(ink, vertical=False)) if low * s <= h <= high * s]
    f = cells.text_height / max(heights) if heights else cells.pixels_per_mm / s
    image = image.resize((max(1, round(image.width * f)), max(1, round(image.height * f))), Image.Resampling.LANCZOS)
    image = raster.ink_mask(image, raster.otsu(image.histogram())).point(lambda v: raster.WHITE - v)
    pad = round(cells.pixels_per_mm)  # a millimetre of paper around the text

    def to_page(x: float, y: float) -> tuple[float, float]:
        return place((x - pad) / f + x0, (y - pad) / f + y0)

    return tesseract.words(
        ImageOps.expand(image, border=pad, fill=raster.WHITE),
        cells.languages,
        cells.page_segmentation,
        cells.min_word_confidence,
        to_page,
        cells.characters.get(field),
    )


def _bands(rows: list[float]) -> list[int]:
    """Heights of the runs of rows with ink: the lines of text of a cell."""
    heights, run = [], 0
    for share in [*rows, 0.0]:
        if share > 0:
            run += 1
        elif run:
            heights.append(run)
            run = 0
    return heights


def wipe_lines(image: Image.Image, level: int, s: float, rules: RegionRules) -> Image.Image:
    """``image`` (``s`` pixels per mm) with the ruled lines that cross it from edge to edge painted over with paper,
    their blurred edges as far as ``line_band`` too."""
    mask = raster.ink_mask(image, level)
    clean = image.copy()
    draw = ImageDraw.Draw(clean)
    band = round(rules.line_band * s)
    for x in raster.runs(raster.profile(mask, vertical=True), rules.wipe_ink, rules.max_line_width * s):
        draw.rectangle([(x - band, 0), (x + band, image.height)], fill=raster.WHITE)
    for y in raster.runs(raster.profile(mask, vertical=False), rules.wipe_ink, rules.max_line_width * s):
        draw.rectangle([(0, y - band), (image.width, y + band)], fill=raster.WHITE)
    return clean
