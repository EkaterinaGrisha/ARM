"""Ruled lines of a page image: the title block and the specification table of a raster or a scan.

GOST 2.104 and GOST 2.106 fix where the lines of a sheet are, so these regions are found by their lines and not by
words a recognizer may miss. A title block is the placement of a form of GOST 2.104 whose main lines are found where
the form puts them, and whose blank places, where another form has a line, have none. The placement comes from the
lines found: a long line is the top or the bottom of the block, a vertical one of its verticals, and two verticals one
block width apart give the scale; else the resolution of the image does. The table of a specification hangs from the
top line of the frame, as wide as the block under it, with its columns where GOST 2.106 puts them and its rows one row
height apart. A sheet turned on the scanner is measured on the lines of the corner of the block and read straightened.

The lines are looked for on a copy of the ink a few pixels per millimetre fine, which is fast and enough for lines a
millimetre apart; the cells are then cut from the image at its own pixels. Pillow only.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from PIL import Image, ImageStat

from kdcheck.config import Forms, RegionRules, RuledForm

Box = tuple[float, float, float, float]
Point = tuple[float, float]
WHITE = 255  # paper in a grey image, ink in a mask


def otsu(hist: list[int]) -> int:
    """The grey level that splits a histogram into ink and paper best (Otsu), the middle one when several do as well:
    a black and white image is split by every level, and a turn or a resize leaves grey between its two."""
    total, weighted = sum(hist), sum(i * h for i, h in enumerate(hist))
    best, levels, below, below_sum = -1.0, [len(hist) // 2], 0, 0.0
    for t, count in enumerate(hist):
        below += count
        below_sum += t * count
        if not below or below == total:
            continue
        between = below * (total - below) * (below_sum / below - (weighted - below_sum) / (total - below)) ** 2
        if between > best:
            best, levels = between, [t]
        elif between == best:
            levels.append(t)
    return (levels[0] + levels[-1]) // 2


def ink_mask(gray: Image.Image, level: int) -> Image.Image:
    """Ink as white on black: the pixels of ``gray`` at ``level`` and darker."""
    return gray.point(lambda v: WHITE if v <= level else 0)


def ink_share(mask: Image.Image) -> float:
    return float(ImageStat.Stat(mask).mean[0]) / WHITE


def crop(image: Image.Image, box: Box, fill: int = WHITE) -> Image.Image:
    """``box`` of an image in whole pixels, ``fill`` beyond the image: paper for a grey image, no ink for a mask."""
    x0, y0, x1, y1 = (round(v) for v in box)
    x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)
    inside = (max(0, x0), max(0, y0), min(image.width, x1), min(image.height, y1))
    if inside == (x0, y0, x1, y1):
        return image.crop(inside)
    canvas = Image.new(image.mode, (x1 - x0, y1 - y0), fill)
    if inside[0] < inside[2] and inside[1] < inside[3]:
        canvas.paste(image.crop(inside), (inside[0] - x0, inside[1] - y0))
    return canvas


def _corners(box: Box) -> list[Point]:
    return [(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]


def _bounds(points: Iterable[Point]) -> Box:
    xs, ys = zip(*points, strict=True)
    return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class Turn:
    """A sheet turned on the scanner: turning the image ``angle`` degrees counter clockwise about ``pivot`` (image
    pixels) straightens it."""

    pivot: Point
    angle: float = 0.0

    def original(self, x: float, y: float) -> Point:
        """The image point of a point of the straightened image."""
        a, (px, py) = math.radians(self.angle), self.pivot
        dx, dy = x - px, y - py
        return px + dx * math.cos(a) - dy * math.sin(a), py + dx * math.sin(a) + dy * math.cos(a)

    def box(self, box: Box) -> Box:
        """The image box that holds ``box`` of the straightened image."""
        return _bounds(self.original(x, y) for x, y in _corners(box))

    def window(self, box: Box) -> Box:
        """The image box that holds ``box`` of the straightened image and the image pixels that turn into it."""
        x0, y0, x1, y1 = _bounds([*_corners(box), *_corners(self.box(box))])
        return math.floor(x0) - 1, math.floor(y0) - 1, math.ceil(x1) + 1, math.ceil(y1) + 1

    def crop(self, image: Image.Image, box: Box, fill: int = WHITE) -> Image.Image:
        """``box`` of the straightened ``image``; ``fill`` where it reaches beyond the image."""
        if not self.angle:
            return crop(image, box, fill)
        # one window holds the box and the pixels that turn into it, so turning it about the pivot keeps both
        window = self.window(box)
        center = (self.pivot[0] - window[0], self.pivot[1] - window[1])
        turned = crop(image, window, fill).rotate(
            self.angle, resample=Image.Resampling.BICUBIC, center=center, fillcolor=fill
        )
        return crop(turned, (box[0] - window[0], box[1] - window[1], box[2] - window[0], box[3] - window[1]), fill)


@dataclass(frozen=True)
class Placement:
    """A form on a page: the page point of (u, v) mm from its top left corner is (x0, y0) + s · (u, v) turned by
    ``angle`` degrees clockwise, which undoes the turn that straightens the sheet."""

    x0: float
    y0: float
    s: float  # page units per mm
    angle: float = 0.0

    def point(self, u: float, v: float) -> Point:
        a = math.radians(self.angle)
        du, dv = u * self.s, v * self.s
        return self.x0 + du * math.cos(a) - dv * math.sin(a), self.y0 + du * math.sin(a) + dv * math.cos(a)

    def rect(self, box: Box) -> Box:
        """The page box that holds ``box``, mm from the corner of the form."""
        return _bounds(self.point(u, v) for u, v in _corners(box))


def skew(small: Image.Image, rules: RegionRules) -> float:
    """Degrees to turn the ink of ``small`` counter clockwise to line up its ruled lines with the rows and columns of
    pixels; ``small`` is ink density at ``sample`` pixels per mm."""

    def lined_up(angle: float) -> float:
        # nearest pixels: an interpolating turn blurs the ink and would favour the angle that turns nothing
        turned = small.rotate(angle, resample=Image.Resampling.NEAREST)
        rows = turned.resize((1, turned.height), Image.Resampling.BOX)
        cols = turned.resize((turned.width, 1), Image.Resampling.BOX)
        return float(ImageStat.Stat(rows).var[0] + ImageStat.Stat(cols).var[0])

    coarse, fine = rules.skew_steps
    steps, finer = round(rules.max_skew / coarse), round(coarse / fine)
    best = max((k * coarse for k in range(-steps, steps + 1)), key=lined_up)
    best = max((best + k * fine for k in range(-finer, finer + 1)), key=lined_up)
    return round(best, 3) + 0.0  # no «-0.0»


def _shrink(ink: Image.Image, k: float) -> Image.Image:
    """Ink density of a mask ``k`` times as fine."""
    return ink.resize((max(1, round(ink.width * k)), max(1, round(ink.height * k))), Image.Resampling.BOX)


def straight_ink(gray: Image.Image, turn: Turn, box: Box, level: int, k: float, rules: RegionRules) -> Image.Image:
    """The ink of ``box`` of the straightened image as a mask ``k`` times as fine: a pixel is ink when that much of
    it is ink. Grain fills such a pixel in part and a ruled line wholly, so the share goes up until lines lie on paper:
    a mask full of grain has ink along any line and paper beside none."""
    window = turn.window(box)
    small = _shrink(ink_mask(crop(gray, window), level), k)
    local = Turn(((turn.pivot[0] - window[0]) * k, (turn.pivot[1] - window[1]) * k), turn.angle)
    part = local.crop(
        small,
        ((box[0] - window[0]) * k, (box[1] - window[1]) * k, (box[2] - window[0]) * k, (box[3] - window[1]) * k),
        fill=0,
    )

    def at_least(density: float) -> Image.Image:
        return part.point(lambda v: WHITE if v >= density * WHITE else 0)

    masks = map(at_least, rules.ink_density)
    return next((mask for mask in masks if ink_share(mask) <= rules.max_mask_ink), at_least(rules.ink_density[-1]))


def profile(mask: Image.Image, *, vertical: bool) -> list[float]:
    """Share of ink across a mask: of each column when ``vertical``, else of each row."""
    size = (mask.width, 1) if vertical else (1, mask.height)
    return [v / WHITE for v in mask.resize(size, Image.Resampling.BOX).tobytes()]  # one byte a pixel


def runs(values: list[float], level: float, widest: float) -> list[float]:
    """Centres of the runs of ``values`` at ``level`` and above that are at most ``widest`` long."""
    found, start = [], None
    for i, v in enumerate([*values, 0.0]):
        if v >= level and start is None:
            start = i
        elif v < level and start is not None:
            if i - start <= widest:
                found.append((start + i - 1) / 2)
            start = None
    return found


def coverage(mask: Image.Image, line: Box, band: float) -> float:
    """Share of the length of a horizontal or vertical ``line`` (mask pixels) with ink within ``band`` of it."""
    x0, y0, x1, y1 = line
    if y0 == y1:
        strip = crop(mask, (x0, y0 - band, x1, y0 + band + 1), fill=0)
        hits = strip.resize((strip.width, 1), Image.Resampling.BOX).tobytes()
    else:
        strip = crop(mask, (x0 - band, y0, x0 + band + 1, y1), fill=0)
        hits = strip.resize((1, strip.height), Image.Resampling.BOX).tobytes()
    return sum(v > 0 for v in hits) / len(hits)


@dataclass(frozen=True)
class Block:
    """A title block on the straightened image: its form, its top left corner, pixels per mm and its size in mm."""

    form: int | None  # a form of the settings; None for form 2a of the later sheets
    x0: float
    y0: float
    s: float
    score: float  # share of the ruled lines of the form found, and of its blank places without one
    width: float
    height: float

    @property
    def x1(self) -> float:
        return self.x0 + self.width * self.s

    @property
    def y1(self) -> float:
        return self.y0 + self.height * self.s


@dataclass(frozen=True)
class Corner:
    """The bottom right corner of a page image, straightened, and the title block in it."""

    turn: Turn
    level: int  # grey level that splits ink from paper
    block: Block


def find_corner(gray: Image.Image, ppmm: float, forms: Forms, rules: RegionRules) -> Corner | None:
    """The title block of forms 1, 2 or 2a of GOST 2.104 in the bottom right corner of a page image; None without one.

    Lines beyond the image do not count, so a sheet shifted off the glass still places its block; of the hypotheses
    the best fitting one wins, and of two that fit as well the one with more lines on the image. Each step of the
    search tries ``max_placements`` of them at most: hatching gives lines without end, and a page must not cost minutes.
    """
    template = forms.title_block
    width, height = rules.corner
    box: Box = (max(0, gray.width - width * ppmm), max(0, gray.height - height * ppmm), gray.width, gray.height)
    corner = crop(gray, box)
    level = otsu(corner.histogram())
    ink = ink_mask(corner, level)
    if ink_share(ink) > rules.line_ink:  # a dark corner, not lines on paper
        return None
    k = rules.sample / ppmm  # mask pixels per image pixel
    turn = Turn(pivot=((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), angle=skew(_shrink(ink, k), rules))
    reach = math.hypot(box[2] - box[0], box[3] - box[1]) / 2 * math.sin(math.radians(rules.max_skew))
    window: Box = (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)
    mask = straight_ink(gray, turn, window, level, k, rules)

    def visible(line: Box) -> Box | None:
        """The part of a line of the mask on the image, when that is most of it."""
        x0, y0, x1, y1 = line
        a = turn.original(window[0] + x0 / k, window[1] + y0 / k)
        b = turn.original(window[0] + x1 / k, window[1] + y1 / k)
        t0, t1 = _clip(a, b, gray.size)
        if t1 - t0 < rules.line_on_image:
            return None
        return x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0, x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1

    nominal = rules.sample  # mask pixels per mm at the resolution of the image
    lowest, highest = template.scale_range
    widest = rules.max_line_width * nominal
    near, far = (v * nominal for v in rules.edge_band)
    anchors = []
    for y in runs(profile(mask, vertical=False), rules.line_ink, widest):
        for (top, bottom), anchor in (((y - far, y - near), "bottom"), ((y + near, y + far), "top")):
            strip = crop(mask, (0, top, mask.width, bottom), fill=0)
            verticals = runs(profile(strip, vertical=True), rules.line_ink, widest)
            widths = {b - a for a in verticals for b in verticals if b > a}
            anchors.append((y, top, bottom, anchor, verticals, widths))
    # forms 1 and 2 before form 2a of the later sheets, which has few lines; the resolution of the image and the
    # scales near it before any scale in the range
    near_nominal = (1 - rules.scale_tolerance, 1 + rules.scale_tolerance)
    first: list[tuple[int | None, RuledForm]] = list(template.forms.items())
    later: list[tuple[int | None, RuledForm]] = [(None, template.continuation)]

    def placements(
        candidates: list[tuple[int | None, RuledForm]], low: float, high: float
    ) -> Iterator[tuple[int | None, RuledForm, float, float, float]]:
        """Where the lines found put the ``candidates``, at the scales within ``low`` and ``high``, each place once."""
        tried: set[tuple[int | None, int, int, int]] = set()
        for y, top, bottom, anchor, verticals, widths in anchors:
            scales = {nominal} | {w / template.width for w in widths if low <= w / template.width / nominal <= high}
            for number, form in candidates:
                for s in scales:
                    y0 = y if anchor == "top" else y - form.height * s
                    band = ((top - y0) / s, (bottom - y0) / s)  # where the verticals were looked for, mm in the form
                    uprights = [u0 for u0, v0, u1, v1 in form.lines if u0 == u1 and v0 <= band[0] and v1 >= band[1]]
                    for x in verticals:
                        for u in uprights:
                            x0 = x - u * s
                            key = (
                                number,
                                round(x0 / nominal),
                                round(y0 / nominal),
                                round(s * template.width / nominal),
                            )
                            if key not in tried:
                                tried.add(key)
                                yield number, form, x0, y0, s

    best: tuple[float, int, Block] | None = None
    for candidates, (low, high) in itertools.product((first, later), (near_nominal, (lowest, highest))):
        for number, form, x0, y0, s in itertools.islice(placements(candidates, low, high), rules.max_placements):
            scored = _form_score(mask, form, x0, y0, s, rules, visible)
            if scored is None or scored[0] < rules.min_form_lines:
                continue
            if best is None or scored > best[:2]:
                found = Block(
                    number,
                    window[0] + x0 / k,
                    window[1] + y0 / k,
                    s / k,
                    scored[0],
                    template.width,
                    form.height,
                )
                best = (*scored, found)
        if best is not None:
            break
    return Corner(turn, level, best[2]) if best is not None else None


def _clip(a: Point, b: Point, size: tuple[int, int]) -> tuple[float, float]:
    """The part of the segment from ``a`` to ``b`` inside an image of ``size``: from t0 to t1 of its length."""
    t0, t1 = 0.0, 1.0
    (ax, ay), (dx, dy) = a, (b[0] - a[0], b[1] - a[1])
    for p, q in ((-dx, ax), (dx, size[0] - ax), (-dy, ay), (dy, size[1] - ay)):
        if p == 0:
            if q < 0:
                return 0.0, 0.0
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    return (t0, t1) if t0 < t1 else (0.0, 0.0)


def _form_score(
    mask: Image.Image,
    form: RuledForm,
    x0: float,
    y0: float,
    s: float,
    rules: RegionRules,
    visible: Callable[[Box], Box | None],
) -> tuple[float, int] | None:
    """Share of the lines of ``form`` placed at (x0, y0) found, and of its blank places without one, and how many
    lines were on the image. Only the part of a line on the image counts, and a line mostly beyond it does not; with
    too few lines left there is no score."""

    def placed(lines: tuple[Box, ...]) -> list[Box]:
        found = (visible((x0 + u0 * s, y0 + v0 * s, x0 + u1 * s, y0 + v1 * s)) for u0, v0, u1, v1 in lines)
        return [line for line in found if line is not None]

    lines, blanks = placed(form.lines), placed(form.blank)
    if len(lines) < rules.min_form_lines * len(form.lines):
        return None
    hits = sum(_ruled(mask, line, s, rules) for line in lines)
    clear = sum(coverage(mask, line, rules.line_band * s) < rules.line_ink for line in blanks)
    return (hits + clear) / (len(lines) + len(blanks)), len(lines)


def _ruled(mask: Image.Image, line: Box, s: float, rules: RegionRules) -> bool:
    """Whether a ruled line lies here: ink along it and paper beside it on one side at least. Grain or a dark area
    has ink along any line, and on both sides of it."""
    band, side = rules.line_band * s, rules.line_side * s
    if coverage(mask, line, band) < rules.line_ink:
        return False
    x0, y0, x1, y1 = line
    dx, dy = (side, 0.0) if x0 == x1 else (0.0, side)
    return any(
        coverage(mask, (x0 + k * dx, y0 + k * dy, x1 + k * dx, y1 + k * dy), band) < rules.line_ink for k in (-1, 1)
    )


@dataclass
class Table:
    """The table of a specification on the straightened image: where its columns and rows are."""

    left: float  # straightened pixels: the left line of the table
    s: float  # pixels per mm
    columns: dict[str, tuple[float, float]]  # left and right edge, mm from the left line
    rows: list[tuple[float, float]] = field(default_factory=list)  # straightened pixels: top and bottom of each row
    other_form: bool = False  # with the column lines of another form, which are not read as form 1


def find_table(gray: Image.Image, found: Corner, forms: Forms, rules: RegionRules) -> Table | None:
    """The table of form 1 of GOST 2.106 above a title block of form 2 or 2a; None without its column lines.

    The table is as wide as the block and ends at it. Its header is one header height under the top line of the
    frame, and its rows one row height each.
    """
    block, template = found.block, forms.specification
    if block.form not in (2, None):
        return None
    k = rules.sample / block.s  # mask pixels per image pixel
    s, margin = rules.sample, rules.max_line_width * rules.sample  # in mask pixels
    window: Box = (block.x0 - margin / k, 0, block.x1 + margin / k, block.y0)
    mask = straight_ink(gray, found.turn, window, found.level, k, rules)
    inner = crop(mask, (margin, 0, mask.width - margin, mask.height), fill=0)
    lines = runs(profile(inner, vertical=False), rules.line_ink, margin)
    if not lines:
        return None
    band, height = rules.line_band * s, template.header_height * s
    # the first long line one header height under another, as the edge of a lid may lie above the frame
    pairs = (y for y in lines if any(abs(y - top - height) <= band * 2 for top in lines))
    header = next(pairs, lines[0] + height)
    body = crop(mask, (0, header + margin, mask.width, mask.height - margin), fill=0)

    def present(edge: float) -> bool:
        return coverage(body, (margin + edge * s, 0, margin + edge * s, body.height), band) >= rules.line_ink

    columns: dict[str, tuple[float, float]] = template.columns.model_dump()
    edges = sorted({edge for pair in columns.values() for edge in pair} - {0.0, forms.title_block.width})
    if sum(map(present, edges)) < len(edges) - 1:
        return None
    table = Table(block.x0, block.s, columns, other_form=any(map(present, template.other_columns)))
    step, y = template.row_height * s, header
    while y + step / 2 <= mask.height:
        table.rows.append((window[1] + y / k, window[1] + (y + step) / k))
        y += step
    return table
