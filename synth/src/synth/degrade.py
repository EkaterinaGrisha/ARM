"""Rasters and scans of rendered sheets, by the life of a paper copy (ADR-0013).

A document becomes pixels one of two ways. A raster comes straight from the file: rendered at a resolution, kept grey
or cut to black and white, compressed. A scan goes through a paper copy first. On paper the strokes spread or thin,
the ink fades in patches, the paper darkens and stains, stamps land on it, a sheet larger than A4 comes folded. On the
scanner the sheet lies turned and off its place, an edge that lifts casts a shadow, dust sits on the glass, the optics
blur and the sensor adds noise; the driver may remove the background, so that clean paper comes out white. The file
then cuts the grey to black and white, which breaks faded strokes and keeps specks of stained paper, or compresses it
with JPEG.

The parameters come from a class of quality (:data:`CLASSES`), drawn with the seed of the document and written into
its truth (:class:`synth.truth.Capture`): the same seed gives the same file, and a metric is sliced by class. ``good``,
``fair`` and ``poor`` stay readable by a person, as the case promises of its scans; ``unreadable`` goes below that, for
the problem file of a mutation. The boxes of the truth follow each sheet through its turn and shift.

Pillow and the generator of the standard library only, as the rest of :mod:`synth`.
"""

from __future__ import annotations

import io
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from synth import vocab
from synth.model import Output
from synth.render import Box
from synth.scans import noise_plane
from synth.truth import AugraphyCapture, Capture, CaptureRecord, Fold, Shadow, SheetCapture, Stamp
from synth.truth import Box as TruthBox

Rect = tuple[float, float, float, float]
# The entry of a degrader, as :func:`capture` takes it: the file of a document, the output it goes to, the seed of the
# document and the boxes of its truth, which move with their sheets. :mod:`synth.augraphy_scans` takes it as well.
Capturer = Callable[[bytes, Output, int, dict[str, Box] | None], tuple[bytes, CaptureRecord]]

STAMP_FONT = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"
STAMP_INKS = ((95, 60, 160), (40, 70, 170))  # violet and blue
LUMA = (0.299, 0.587, 0.114)  # weights of red, green and blue in the grey of a grey scan
INK_COLOUR, PAPER_COLOUR = (0, 6, 28), (255, 248, 229)  # a colour scan: blue-black ink on cream paper
MM_PER_INCH = 25.4


@dataclass(frozen=True)
class Ranges:
    """What a class of quality allows: a pair is a range drawn uniformly, ``dpi`` and ``modes`` are sets of choices."""

    dpi: tuple[int, ...]  # scanner settings
    modes: tuple[str, ...]  # black and white, grey, colour, as often as listed
    jpeg: tuple[int, int]  # JPEG quality
    cut: tuple[float, float]  # where black and white splits ink from paper: share of the way from ink to stained paper
    ink: tuple[int, int]  # grey level of the ink, 0 is black
    paper: tuple[int, int]  # grey level of clean paper
    spread: tuple[float, float]  # mm a stroke grows by on each side; it thins below zero
    fade: tuple[float, float]  # share of the ink lost where it fades most
    stains: tuple[int, int]  # grey levels the paper darkens by in its darkest stain
    stamp: float  # chance of a stamp on a sheet
    fold: float  # chance that a sheet larger than A4 comes folded; an A4 sheet a quarter as often
    skew: float  # largest turn of a sheet, degrees
    shift: float  # largest shift of a sheet, mm
    shadow: float  # chance of a shadow along an edge
    dust: tuple[float, float]  # specks on the glass per 100 cm²
    blur: tuple[float, float]  # sigma of the optics, mm
    noise: tuple[float, float]  # sigma of the sensor noise, grey levels
    dark_lid: float  # chance of a dark lid around the sheet
    whiten: float  # chance that the scanner removes the background of a grey or colour scan


MODES = ("bilevel", "bilevel", "grey", "grey", "colour")
CLASSES = {
    "good": Ranges(
        dpi=(300, 400, 600),
        modes=MODES,
        jpeg=(85, 95),
        cut=(0.45, 0.6),
        ink=(0, 30),
        paper=(238, 255),
        spread=(-0.02, 0.04),
        fade=(0.0, 0.1),
        stains=(0, 6),
        stamp=0.2,
        fold=0.2,
        skew=0.3,
        shift=1.0,
        shadow=0.0,
        dust=(0.0, 0.5),
        blur=(0.02, 0.04),
        noise=(1.0, 3.0),
        dark_lid=0.0,
        whiten=0.6,
    ),
    "fair": Ranges(
        dpi=(200, 300),
        modes=MODES,
        jpeg=(60, 85),
        cut=(0.35, 0.7),
        ink=(20, 80),
        paper=(220, 248),
        spread=(-0.03, 0.08),
        fade=(0.1, 0.35),
        stains=(4, 14),
        stamp=0.5,
        fold=0.6,
        skew=1.0,
        shift=3.0,
        shadow=0.3,
        dust=(0.5, 3.0),
        blur=(0.04, 0.07),
        noise=(3.0, 7.0),
        dark_lid=0.2,
        whiten=0.5,
    ),
    "poor": Ranges(
        dpi=(150, 200),
        modes=MODES,
        jpeg=(35, 60),
        cut=(0.3, 0.75),
        ink=(60, 120),
        paper=(200, 235),
        spread=(-0.05, 0.14),
        fade=(0.3, 0.55),
        stains=(10, 28),
        stamp=0.8,
        fold=0.9,
        skew=2.5,
        shift=6.0,
        shadow=0.6,
        dust=(2.0, 8.0),
        blur=(0.07, 0.11),
        noise=(6.0, 12.0),
        dark_lid=0.4,
        whiten=0.3,
    ),
    # Below what a person reads: strokes of 0.35 mm under a blur of 0.6 mm, 30 grey levels between ink and paper.
    "unreadable": Ranges(
        dpi=(150,),
        modes=("grey",),
        jpeg=(35, 45),
        cut=(0.5, 0.5),
        ink=(105, 115),
        paper=(135, 145),
        spread=(0.0, 0.0),
        fade=(0.3, 0.5),
        stains=(0, 6),
        stamp=0.0,
        fold=0.0,
        skew=0.8,
        shift=1.0,
        shadow=0.0,
        dust=(0.0, 1.0),
        blur=(0.6, 0.75),
        noise=(6.0, 9.0),
        dark_lid=0.0,
        whiten=0.0,
    ),
}
RASTER_MODES = ("bilevel", "grey")  # a raster from CAD: black and white, as archives keep drawings, or grey


def capture(pdf: bytes, output: Output, seed: int, boxes: dict[str, Box] | None = None) -> tuple[bytes, Capture]:
    """The file of a document captured as ``output`` asks, and how it was made; ``boxes`` move with their sheets."""
    document = pdfium.PdfDocument(pdf)
    sizes = [_mm(page.get_size()) for page in document]
    if output.container in ("jpg", "png") and len(sizes) > 1:
        raise ValueError(f"{output.container} holds one page, the document has {len(sizes)}")
    rng = random.Random(seed)
    record = plan(output, sizes, seed, rng)
    sheets: list[SheetCapture | None] = list(record.sheets) or [None] * len(sizes)
    images = [_pixels(page, record, sheet, rng) for page, sheet in zip(document, sheets, strict=True)]
    if boxes is not None:
        for key, (page, box) in boxes.items():
            boxes[key] = (page, move(box, sheets[page - 1], sizes[page - 1]))
    return encode(images, record), record


def plan(output: Output, sizes: list[tuple[float, float]], seed: int, rng: random.Random) -> Capture:
    """Every parameter of the capture, drawn from the class of ``output`` for sheets of ``sizes`` mm."""
    if output.kind == "vector" or not output.quality:
        raise ValueError(f"no class of quality to capture {output}")
    r = CLASSES[output.quality]
    mode = rng.choice(r.modes if output.kind == "scan" else RASTER_MODES)
    dpi = rng.choice(r.dpi)
    lossy = output.container == "jpg" or (output.container == "pdf" and mode != "bilevel")
    record = Capture.model_validate(
        {
            "kind": output.kind,
            "quality": output.quality,
            "seed": seed,
            "dpi": dpi,
            "mode": mode,
            "container": output.container,
            "jpeg": rng.randint(*r.jpeg) if lossy else None,
            "threshold": 128 if mode == "bilevel" else None,
        }
    )
    if output.kind == "raster":
        return record
    ink, paper, stains = rng.randint(*r.ink), rng.randint(*r.paper), rng.randint(*r.stains)
    fade, noise = round(rng.uniform(*r.fade), 2), round(rng.uniform(*r.noise), 1)
    # background removal of a scanner driver: clean paper and its noise turn white, stains and ink stay
    white = round(paper - 2.5 * noise) if mode != "bilevel" and rng.random() < r.whiten else None
    # The threshold keeps the sheet legible, as the case promises of its scans: ink faded by up to nine tenths of
    # ``fade`` stays black, and only the deepest patches break.
    cut = max(rng.uniform(*r.cut), 0.9 * fade + 0.1)
    return record.model_copy(
        update={
            "threshold": round(ink + cut * (paper - stains - ink)) if mode == "bilevel" else None,
            "ink": ink,
            "paper": paper,
            "spread": round(rng.uniform(*r.spread), 3),
            "fade": fade,
            "stains": stains,
            "blur": round(rng.uniform(*r.blur), 3),
            "noise": noise,
            "white": white,
            "lid": rng.randint(25, 60) if rng.random() < r.dark_lid else 255,
            "sheets": [_sheet(r, size, page, rng) for page, size in enumerate(sizes, start=1)],
        }
    )


def _sheet(r: Ranges, size: tuple[float, float], page: int, rng: random.Random) -> SheetCapture:
    w, h = size
    sheet = SheetCapture(
        skew=round(rng.uniform(-r.skew, r.skew), 2),
        shift=(round(rng.uniform(-r.shift, r.shift), 1), round(rng.uniform(-r.shift, r.shift), 1)),
        dust=round(rng.uniform(*r.dust) * w * h / 10_000),
    )
    if rng.random() < (r.fold if max(w, h) > 300 else r.fold / 4):  # folded to A4 for the archive, an A4 in half
        landscape = w > h
        at = (w if landscape else h) / 2 + rng.uniform(-3, 3)
        sheet.folds.append(Fold(axis="x" if landscape else "y", at=round(at, 1), shade=rng.randint(15, 60)))
    if rng.random() < r.shadow:
        side = rng.choice(("left", "right", "top", "bottom"))
        sheet.shadow = Shadow(side=side, width=round(rng.uniform(3, 12), 1), depth=rng.randint(20, 80))
    if rng.random() < r.stamp:
        sheet.stamps.append(_stamp(sheet, size, page, rng))
        if rng.random() < r.stamp / 3:
            sheet.stamps.append(_stamp(sheet, size, page, rng))
    return sheet


def _stamp(sheet: SheetCapture, size: tuple[float, float], page: int, rng: random.Random) -> Stamp:
    """A stamp where archives put them: on the title block, in the left margin, in the top corner or on the field."""
    w, h = size
    shape = "round" if rng.random() < 0.3 else "rect"
    lines = rng.choice(vocab.ROUND_STAMPS if shape == "round" else vocab.STAMPS)
    date = f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.choice(('24', '25'))}"
    text = [line.format(n=rng.randint(10, 9999), date=date) for line in lines]
    if shape == "round":
        d = rng.uniform(24, 34)
        sw, sh = d, d
    else:
        sw, sh = rng.uniform(40, 56), 6.0 + 6.5 * len(text)
    place = rng.choice(("title", "margin", "corner", "field"))
    if place == "title":  # over the upper cells of the title block, where the name and the designation are
        x, y = w - 5 - rng.uniform(20, 120), h - 5 - rng.uniform(20, 60)
    elif place == "margin":
        x, y = 22 + sw / 2, rng.uniform(sh, h - sh)
    elif place == "corner":
        x, y = w - 8 - sw / 2, 8 + sh / 2
    else:
        x, y = rng.uniform(30, w - 30), rng.uniform(20, h - 70)
    # the whole stamp stays on the sheet, whatever its turn
    reach = math.hypot(sw, sh) / 2 + 1
    x, y = min(max(x, reach), w - reach), min(max(y, reach), h - reach)
    angle = round(rng.uniform(-12, 12), 1)
    corners = [_turn((x + dx, y + dy), angle, (x, y)) for dx in (-sw / 2, sw / 2) for dy in (-sh / 2, sh / 2)]
    moved = [_move_point(p, sheet, size) for p in corners]
    box = _shares([p[0] for p in moved], [p[1] for p in moved], size)
    return Stamp(
        text=text,
        shape=shape,
        centre=(round(x, 1), round(y, 1)),
        size=(round(sw, 1), round(sh, 1)),
        angle=angle,
        colour=rng.choice(STAMP_INKS),
        box=TruthBox(page=page, bbox=box),
    )


# ---------------------------------------------------------------------------------------- geometry


def _mm(points: tuple[float, float]) -> tuple[float, float]:
    return points[0] / 72 * MM_PER_INCH, points[1] / 72 * MM_PER_INCH


def _turn(point: tuple[float, float], angle: float, centre: tuple[float, float]) -> tuple[float, float]:
    """``point`` turned about ``centre`` as Pillow turns an image by ``angle`` degrees, counter clockwise on screen."""
    a = math.radians(angle)
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    return centre[0] + dx * math.cos(a) + dy * math.sin(a), centre[1] - dx * math.sin(a) + dy * math.cos(a)


def _move_point(point: tuple[float, float], sheet: SheetCapture, size: tuple[float, float]) -> tuple[float, float]:
    x, y = _turn(point, sheet.skew, (size[0] / 2, size[1] / 2))
    return x + sheet.shift[0], y + sheet.shift[1]


def _shares(xs: list[float], ys: list[float], size: tuple[float, float]) -> Rect:
    w, h = size
    return (
        round(max(0.0, min(xs) / w), 4),
        round(max(0.0, min(ys) / h), 4),
        round(min(1.0, max(xs) / w), 4),
        round(min(1.0, max(ys) / h), 4),
    )


def move(box: Rect, sheet: SheetCapture | None, size: tuple[float, float]) -> Rect:
    """Where a box of the sheet, in shares of it, lands on the scan: the box that holds it turned and shifted."""
    if sheet is None or (not sheet.skew and sheet.shift == (0.0, 0.0)):
        return box
    w, h = size
    x0, y0, x1, y1 = box
    corners = [_move_point((x * w, y * h), sheet, size) for x in (x0, x1) for y in (y0, y1)]
    return _shares([p[0] for p in corners], [p[1] for p in corners], size)


# ---------------------------------------------------------------------------------------- pixels


def _pixels(page: pdfium.PdfPage, record: Capture, sheet: SheetCapture | None, rng: random.Random) -> Image.Image:
    img: Image.Image = page.render(scale=record.dpi / 72, grayscale=True).to_pil().convert("L")
    if sheet is None:
        return _cut(img, record)
    ppmm = record.dpi / MM_PER_INCH
    # the paper copy
    img = _spread(img, record.spread * ppmm)
    img = _fade(img, record.fade, ppmm, rng)
    img = _on_paper(img, record, ppmm, rng)
    for stamp in sheet.stamps:
        _ink_stamp(img, stamp, ppmm, rng)
    for fold in sheet.folds:
        img = _fold(img, fold, ppmm, rng)
    # the scanner
    lid = record.lid if img.mode == "L" else (record.lid,) * 3
    shift = (round(sheet.shift[0] * ppmm), round(sheet.shift[1] * ppmm))
    img = img.rotate(sheet.skew, resample=Image.Resampling.BICUBIC, translate=shift, fillcolor=lid)
    if sheet.shadow:
        img = _shadow(img, sheet.shadow, ppmm)
    _dust(img, sheet.dust, ppmm, rng)
    if record.blur:
        img = img.filter(ImageFilter.GaussianBlur(record.blur * ppmm))
    if record.noise:
        plane = noise_plane(img.size, record.noise, rng)
        img = ImageChops.add(img, plane if img.mode == "L" else plane.convert("RGB"), scale=1.0, offset=-128)
    if record.white:
        white = record.white
        img = img.point(lambda v: min(255, round(v * 255 / white)))
    return _cut(img, record)


def _lut(f: Callable[[int], float]) -> list[int]:
    """A lookup table of the grey levels through ``f``, clipped to 0–255."""
    return [max(0, min(255, round(f(v)))) for v in range(256)]


def _field(size: tuple[int, int], cell: float, rng: random.Random) -> Image.Image:
    """Smooth random grey with blots about ``cell`` pixels across: random cells enlarged bicubically."""
    cols, rows = max(2, round(size[0] / cell)) + 1, max(2, round(size[1] / cell)) + 1
    return Image.frombytes("L", (cols, rows), rng.randbytes(cols * rows)).resize(size, Image.Resampling.BICUBIC)


def _spread(img: Image.Image, d: float) -> Image.Image:
    """Strokes grown by ``d`` pixels on each side, thinner below zero: a blur, then a cut off the middle level.

    A blurred edge crosses the level 255·Φ(d/σ) ``d`` pixels out into the paper, so the cut moves the edge there; a
    stroke thinner than the blur that does not reach the level is gone, as faint lines of a copy break up.
    """
    if abs(d) < 0.1:
        return img
    sigma = max(0.8, abs(d))
    level = 127.5 * (1 + math.erf(d / (sigma * math.sqrt(2))))
    soft = 24  # grey levels the new edge takes from ink to paper
    return img.filter(ImageFilter.GaussianBlur(sigma)).point(_lut(lambda v: 127.5 + (v - level) * 127.5 / soft))


def _fade(img: Image.Image, fade: float, ppmm: float, rng: random.Random) -> Image.Image:
    """Ink lighter in patches a few millimetres across: ``fade`` of it lost where a patch is deepest."""
    if fade <= 0:
        return img
    keep = _field(img.size, 4 * ppmm, rng).point(_lut(lambda f: 255 - fade * 255 * (f / 255) ** 2))
    return ImageChops.invert(ImageChops.multiply(ImageChops.invert(img), keep))


def _on_paper(img: Image.Image, record: Capture, ppmm: float, rng: random.Random) -> Image.Image:
    """``img`` (ink 0, paper 255) as ink of its level on paper of its level, the paper darker in a few stains."""
    stains = _field(img.size, 40 * ppmm, rng).point(_lut(lambda s: record.paper - record.stains * (s / 255) ** 3))
    sheet = Image.composite(stains, Image.new("L", img.size, record.ink), img)
    return ImageOps.colorize(sheet, INK_COLOUR, PAPER_COLOUR) if record.mode == "colour" else sheet


def _ink_stamp(img: Image.Image, stamp: Stamp, ppmm: float, rng: random.Random) -> None:
    """The stamp printed over the sheet: its ink darkens what lies under it, unevenly."""
    w, h = round(stamp.size[0] * ppmm), round(stamp.size[1] * ppmm)
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    line = max(1, round(0.5 * ppmm))
    if stamp.shape == "round":
        draw.ellipse((0, 0, w - 1, h - 1), outline=255, width=line)
    else:
        draw.rectangle((0, 0, w - 1, h - 1), outline=255, width=line)
    size = round(4 * ppmm)  # capitals about 3 mm high
    font = ImageFont.truetype(str(STAMP_FONT), size)
    longest = max(font.getlength(t) for t in stamp.text)
    if longest > 0.8 * w:
        font = ImageFont.truetype(str(STAMP_FONT), max(6, int(size * 0.8 * w / longest)))
    for i, text in enumerate(stamp.text, start=1):
        draw.text((w / 2, h * i / (len(stamp.text) + 1)), text, font=font, fill=255, anchor="mm")
    mask = mask.rotate(stamp.angle, resample=Image.Resampling.BICUBIC, expand=True)
    mask = ImageChops.multiply(mask, _field(mask.size, 3 * ppmm, rng).point(_lut(lambda f: 140 + f * 115 / 255)))
    colour = stamp.colour if img.mode == "RGB" else round(sum(c * k for c, k in zip(stamp.colour, LUMA, strict=True)))
    left, top = round(stamp.centre[0] * ppmm - mask.width / 2), round(stamp.centre[1] * ppmm - mask.height / 2)
    region = img.crop((left, top, left + mask.width, top + mask.height))
    inked = ImageChops.multiply(region, Image.new(img.mode, region.size, colour))
    img.paste(Image.composite(inked, region, mask), (left, top))


def _darken(img: Image.Image, shade: Image.Image) -> Image.Image:
    """``img`` times ``shade`` (255 leaves it as is), on every band."""
    return ImageChops.multiply(img, shade if img.mode == "L" else shade.convert("RGB"))


def _band(img: Image.Image, profile: list[float], along_x: bool) -> Image.Image:
    """``img`` darkened by ``profile``, one value per column when ``along_x``, else one per row."""
    strip = Image.new("L", (len(profile), 1) if along_x else (1, len(profile)))
    strip.putdata([round(v) for v in profile])
    return _darken(img, strip.resize(img.size, Image.Resampling.NEAREST))


def _fold(img: Image.Image, fold: Fold, ppmm: float, rng: random.Random) -> Image.Image:
    """A crease: the paper bends off the glass, darker on one side of the line and lit on the other, and a thin dark
    line runs where the fibres broke, with gaps where they held."""
    along_x = fold.axis == "x"
    at, half = fold.at * ppmm, 5 * ppmm
    across = img.width if along_x else img.height
    img = _band(
        img,
        [255 - fold.shade * math.exp(-4 * ((i - at) / half) ** 2) * (1.0 if i < at else 0.35) for i in range(across)],
        along_x,
    )
    crease = Image.new("L", img.size, 255)
    draw = ImageDraw.Draw(crease)
    run, width, pos = (img.height if along_x else img.width), max(1, round(0.15 * ppmm)), 0.0
    while pos < run:
        end = min(pos + rng.uniform(10, 60) * ppmm, run)
        a, b, c = round(pos), round(end), round(at)
        draw.line((c, a, c, b) if along_x else (a, c, b, c), fill=255 - fold.shade, width=width)
        pos = end + rng.uniform(3, 25) * ppmm
    return _darken(img, crease)


def _shadow(img: Image.Image, shadow: Shadow, ppmm: float) -> Image.Image:
    """A shadow along the edge that lifted off the glass, deepest at the edge."""
    along_x = shadow.side in ("left", "right")
    across = img.width if along_x else img.height
    width = shadow.width * ppmm
    near = shadow.side in ("left", "top")
    return _band(
        img,
        [255 - shadow.depth * max(0.0, 1 - (i if near else across - 1 - i) / width) ** 2 for i in range(across)],
        along_x,
    )


def _dust(img: Image.Image, count: int, ppmm: float, rng: random.Random) -> None:
    """Specks on the glass, 0.1–0.5 mm across."""
    draw = ImageDraw.Draw(img)
    for _ in range(count):
        x, y = rng.uniform(0, img.width), rng.uniform(0, img.height)
        rx, ry = rng.uniform(0.05, 0.25) * ppmm, rng.uniform(0.05, 0.25) * ppmm
        level = rng.randint(20, 90)
        draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=level if img.mode == "L" else (level,) * 3)


def _cut(img: Image.Image, record: Capture) -> Image.Image:
    """The pixels the file keeps: black and white at the threshold, grey, or colour."""
    if record.mode == "bilevel":
        threshold = record.threshold if record.threshold is not None else 128
        return img.convert("L").point(lambda v: 255 if v > threshold else 0, mode="1")
    return img.convert("RGB" if record.mode == "colour" else "L")


def encode(images: list[Image.Image], record: Capture | AugraphyCapture) -> bytes:
    """The pages in the container of ``record``, with their resolution: a PDF of one image per page as a scanner
    writes it (JPEG, or CCITT G4 for black and white), a TIFF (G4 or lossless), a JPEG or a PNG."""
    buf = io.BytesIO()
    first, *rest = images
    dpi = record.dpi
    if record.container == "pdf":
        jpeg = {"quality": record.jpeg} if record.jpeg else {}  # a black and white page goes in as G4, no quality
        # no dates in the PDF, so that the same seed gives the same bytes
        first.save(
            buf, "PDF", resolution=dpi, save_all=True, append_images=rest, creationDate=None, modDate=None, **jpeg
        )
    elif record.container == "tiff":
        compression = "group4" if first.mode == "1" else "tiff_deflate"
        first.save(buf, "TIFF", compression=compression, dpi=(dpi, dpi), save_all=True, append_images=rest)
    elif record.container == "jpg":
        first.convert("RGB" if first.mode == "RGB" else "L").save(buf, "JPEG", quality=record.jpeg, dpi=(dpi, dpi))
    else:
        first.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()
