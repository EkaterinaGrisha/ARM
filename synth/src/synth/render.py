"""Sheets of ESKD documents drawn with reportlab (BSD): the frame, the title block, specifications and drawings.

Pages are drawn in the bundled OpenGost fonts, the lettering of GOST 2.304, type A or type B as the document says, with
no timestamps, so the same document gives the same bytes on any machine. Coordinates are millimetres from the top left
corner of the sheet.
"""

from __future__ import annotations

import functools
import io
from itertools import pairwise
from pathlib import Path

from reportlab import rl_config
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from synth.model import Drawing, FontType, Row, Spec, Variant

MM = 72 / 25.4
FORMATS = {"A4": (210, 297), "A3": (420, 297)}

# Where a value is drawn: the page from 1, and x0, y0, x1, y1 as shares of the page from its top left corner,
# as kdcheck.schemas.SourceRef.bbox counts them.
Box = tuple[int, tuple[float, float, float, float]]
DESCENT = 0.3  # below the baseline, as a share of the height of capitals

# OpenGost Type A and Type B 0.3 as the openSUSE package opengost-ttf-fonts 0.3-7.32 ships them, byte for byte; sources
# at https://codeberg.org/nadevko/opengostfonts. SIL Open Font License 1.1, text in fonts/OFL.txt.
FONTS: dict[FontType, tuple[str, Path]] = {
    "A": ("OpenGostTypeA", Path(__file__).parent / "fonts" / "OpenGostTypeA-Regular.ttf"),
    "B": ("OpenGostTypeB", Path(__file__).parent / "fonts" / "OpenGostTypeB-Regular.ttf"),
}
FONT = FONTS["A"][0]
# Type B has no «Ø» and no «↗»: a CAD system takes such signs from a font of symbols, the sheets from type A.
FALLBACK: FontType = "A"


@functools.cache
def _ttf(font: FontType = "A") -> TTFont:
    name, path = FONTS[font]
    ttf = TTFont(name, str(path))
    pdfmetrics.registerFont(ttf)
    return ttf


def _font(font: FontType = "A") -> str:
    _ttf(font)
    return FONTS[font][0]


def _cap(font: FontType) -> float:
    return float(_ttf(font).face.capHeight) / 1000  # TrueType metrics come per 1000 units of the size


def font_size(size: float, font: FontType = "A") -> float:
    """Font size in points that makes capitals ``size`` mm high, by the capital height of the font."""
    return size * MM / _cap(font)


def _runs(s: str, font: FontType) -> list[tuple[FontType, str]]:
    """``s`` in runs of one font: the glyphs ``font`` lacks come from the fallback."""
    glyphs = _ttf(font).face.charToGlyph
    runs: list[tuple[FontType, str]] = []
    for ch in s:
        f = font if ord(ch) in glyphs else FALLBACK
        if runs and runs[-1][0] == f:
            runs[-1] = (f, runs[-1][1] + ch)
        else:
            runs.append((f, ch))
    return runs


def _run_size(fontsize: float, font: FontType, run: FontType) -> float:
    """The size of a run of the fallback that gives its capitals the height of those of ``font``."""
    return fontsize if run == font else fontsize * _cap(font) / _cap(run)


def _width_pt(s: str, font: FontType, fontsize: float) -> float:
    return sum(pdfmetrics.stringWidth(t, _font(f), _run_size(fontsize, font, f)) for f, t in _runs(s, font))


def text_width(s: str, size: float = 3.5, font: FontType = "A") -> float:
    """Width in mm of ``s`` in the drafting lettering ``font``, capitals ``size`` mm high."""
    return _width_pt(s, font, font_size(size, font)) / MM


def new_canvas(password: str | None = None, font: FontType = "A") -> Canvas:
    """A canvas that writes the same bytes for the same drawing: no timestamps, a fixed document ID.

    With ``password`` the PDF opens only with it.
    """
    _font(FALLBACK)
    rl_config.useA85 = 0  # binary streams as is, as CAD systems and scanners write them, not a quarter larger
    # The initial font of each page, else every page refers to an unused Helvetica. The stub types it as a float.
    return Canvas(
        io.BytesIO(),
        invariant=1,
        pageCompression=1,
        initialFontName=_font(font),  # type: ignore[arg-type]
        encrypt=password,
    )


class Sheet:
    """A page with coordinates in millimetres from the top left corner, its text in ``font``.

    Text drawn with a ``key`` leaves its box in ``boxes``; text drawn twice under one key on a page takes the union.
    """

    def __init__(
        self,
        canvas: Canvas,
        fmt: str,
        *,
        page: int = 1,
        boxes: dict[str, Box] | None = None,
        font: FontType = "A",
    ) -> None:
        self.w, self.h = FORMATS[fmt]
        self.c = canvas
        self.page, self.boxes, self.font = page, boxes, font
        canvas.setPageSize((self.w * MM, self.h * MM))

    def _pt(self, x: float, y: float) -> tuple[float, float]:
        """Millimetres from the top left corner → points from the bottom left corner, as PDF counts."""
        return x * MM, (self.h - y) * MM

    def line(self, x0: float, y0: float, x1: float, y1: float, width: float = 0.25) -> None:
        self.c.setLineWidth(width * MM)
        self.c.line(*self._pt(x0, y0), *self._pt(x1, y1))

    def rect(self, x0: float, y0: float, x1: float, y1: float, width: float = 0.25) -> None:
        self.c.setLineWidth(width * MM)
        self.c.rect(*self._pt(x0, y1), (x1 - x0) * MM, (y1 - y0) * MM)

    def circle(
        self, cx: float, cy: float, r: float, width: float = 0.5, dashes: tuple[float, float] | None = None
    ) -> None:
        """``dashes``: dash and gap in points."""
        self.c.saveState()
        self.c.setLineWidth(width * MM)
        if dashes:
            self.c.setDash(list(dashes))
        self.c.circle(*self._pt(cx, cy), r * MM)
        self.c.restoreState()

    def width(self, s: str, size: float = 3.5) -> float:
        return text_width(s, size, self.font)

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 3.5,
        rotate: int = 0,
        max_width: float | None = None,
        key: str | None = None,
    ) -> None:
        """Text with the baseline starting at (x, y); ``size`` is the height of capitals in mm.

        Rotated text runs bottom to top, as in the columns of the left margin.
        """
        if not s:
            return
        fontsize = font_size(size, self.font)
        sx = 1.0
        c = self.c
        c.saveState()
        c.translate(*self._pt(x, y))
        if rotate:
            c.rotate(rotate)
        elif max_width:
            natural = self.width(s, size)
            if natural > max_width:  # squeeze up to a quarter, then make it smaller
                k = max_width / natural
                sx = max(k, 0.75)
                if k < 0.75:
                    fontsize *= k / 0.75
            c.scale(sx, 1)
        at: float = 0
        for font, run in _runs(s, self.font):
            size_pt = _run_size(fontsize, self.font, font)
            c.setFont(_font(font), size_pt)
            c.drawString(at, 0, run)
            at += pdfmetrics.stringWidth(run, _font(font), size_pt)
        c.restoreState()
        if key is not None and self.boxes is not None:
            width = _width_pt(s, self.font, fontsize) * sx / MM
            self._keep(key, x, y, width, fontsize / font_size(1.0, self.font), rotate)

    def _keep(self, key: str, x: float, y: float, width: float, height: float, rotate: int) -> None:
        if rotate:  # runs up the page from (x, y), capitals to the left of the baseline
            x0, y0, x1, y1 = x - height, y - width, x + DESCENT * height, y
        else:
            x0, y0, x1, y1 = x, y - height, x + width, y + DESCENT * height
        box = (
            round(max(0.0, x0 / self.w), 4),
            round(max(0.0, y0 / self.h), 4),
            round(min(1.0, x1 / self.w), 4),
            round(min(1.0, y1 / self.h), 4),
        )
        assert self.boxes is not None
        if key in self.boxes:
            page, (a0, b0, a1, b1) = self.boxes[key]
            if page != self.page:
                return  # a value keeps the box of the page it starts on
            box = (min(a0, box[0]), min(b0, box[1]), max(a1, box[2]), max(b1, box[3]))
        self.boxes[key] = (self.page, box)

    def text_center(
        self, x0: float, x1: float, y_base: float, s: str, size: float = 3.5, key: str | None = None
    ) -> None:
        if not s:
            return
        avail = x1 - x0 - 1.6
        width = min(self.width(s, size), avail)
        self.text(x0 + (x1 - x0 - width) / 2, y_base, s, size, max_width=avail, key=key)

    def shortened(
        self, x: float, y: float, designation: str, written: str, max_width: float = 68, key: str | None = None
    ) -> None:
        """A designation at ``x``, or only its suffix ``written`` where the suffix of the full one would stand: a row
        below the full designation, GOST 2.113, 4.1.12 and 5.3."""
        if written:
            shift = self.width(designation.removesuffix(written))
            x, max_width = x + shift, max_width - shift
        self.text(x, y, written or designation, 3.5, max_width=max_width, key=key)


def _frame(sh: Sheet, archive: str, first_use: str, *, top: bool = True) -> None:
    """The frame and the columns of the left margin; columns 24–25 at the top only on the first sheet of a drawing."""
    sh.rect(20, 5, sh.w - 5, sh.h - 5, width=0.7)
    # columns 19–23 (left margin, bottom) and 24–25 (left margin, top)
    x0, x1, x2 = 8, 13, 20
    labels: list[tuple[str, str, int, str | None]] = [
        ("Инв. № подл.", archive, 25, "archive_number"),
        ("Подп. и дата", "", 35, None),
        ("Взам. инв. №", "", 25, None),
        ("Инв. № дубл.", "", 25, None),
        ("Подп. и дата", "", 35, None),
    ]
    y = sh.h - 5
    for label, value, height, key in labels:
        sh.rect(x0, y - height, x1, y)
        sh.rect(x1, y - height, x2, y)
        sh.text(x0 + 3.6, y - 2, label, 2.5, rotate=90)
        sh.text(x1 + 5, y - 2, value, 3.5, rotate=90, key=key)
        y -= height
    upper: list[tuple[str, str, int, str | None]] = [
        ("Перв. примен.", first_use, 60, "first_use"),
        ("Справ. №", "", 60, None),
    ]
    y = 5
    for label, value, height, key in upper if top else []:
        sh.rect(x0, y, x1, y + height)
        sh.rect(x1, y, x2, y + height)
        sh.text(x0 + 3.6, y + height - 2, label, 2.5, rotate=90)
        sh.text(x1 + 5, y + height - 2, value, 3.5, rotate=90, key=key)
        y += height


def _title_block(
    sh: Sheet,
    d: Drawing | Spec,
    *,
    material: str = "",
    mass: str = "",
    scale: str = "",
    spec_form: bool = False,
    sheets: int = 1,
) -> None:
    """Title block: form 1 for drawings (185×55) or form 2 for specifications (185×40)."""
    height = 40 if spec_form else 55
    X, Y = sh.w - 5 - 185, sh.h - 5 - height
    sh.rect(X, Y, X + 185, Y + height, width=0.7)
    # left part: changes and signatures
    cols = [0, 7, 17, 40, 55, 65]
    for c in cols[1:-1]:
        sh.line(X + c, Y, X + c, Y + 25)
    sh.line(X + 65, Y, X + 65, Y + height, width=0.7)
    for r in range(1, height // 5):
        sh.line(X, Y + r * 5, X + 65, Y + r * 5)
    change_rows = 4 if not spec_form else 4
    # the header of the changes table is the 5th row
    header_y = Y + change_rows * 5
    for (c0, c1), label in zip(pairwise(cols), ["Изм.", "Лист", "№ докум.", "Подп.", "Дата"], strict=True):
        sh.text_center(X + c0, X + c1, header_y + 4, label, 2.5)
    for i, ch in enumerate(sorted(d.changes, key=lambda c: -c.number)[:change_rows]):
        yb = header_y - i * 5 - 1
        sh.text_center(X + cols[0], X + cols[1], yb, str(ch.number), 2.5, key="revision" if i == 0 else None)
        sh.text_center(X + cols[1], X + cols[2], yb, "—", 2.5)
        sh.text(X + cols[2] + 0.8, yb, ch.notice, 2.5, max_width=21.4)
        sh.text_center(X + cols[4], X + cols[5], yb, ch.date, 2.5)
    roles = ["Разраб.", "Пров.", "Т.контр.", "", "Н.контр.", "Утв."]
    names = ["Иванов", "Петров", "", "", "Сидорова", "Кузнецов"]
    for i, (role, name) in enumerate(zip(roles, names, strict=True)):
        yb = header_y + 5 * (i + 1) + 4
        if yb > Y + height:
            break
        sh.text(X + 0.8, yb, role, 2.5, max_width=15.4)
        sh.text(X + 17.8, yb, name, 2.5, max_width=21.4)
    # right part
    sh.line(X + 65, Y + 15, X + 185, Y + 15, width=0.7)
    sh.text_center(X + 65, X + 185, Y + 11, d.designation, 7, key="designation")
    if spec_form:
        sh.line(X + 135, Y + 15, X + 135, Y + 40, width=0.7)
        sh.text(X + 68, Y + 25, d.name, 5, max_width=65, key="name")
        for x in (150, 165):
            sh.line(X + x, Y + 15, X + x, Y + 25)
        sh.line(X + 135, Y + 20, X + 185, Y + 20)
        sh.line(X + 135, Y + 25, X + 185, Y + 25, width=0.7)
        sh.text_center(X + 135, X + 150, Y + 19, "Лит.", 2.5)
        sh.text_center(X + 150, X + 165, Y + 19, "Лист", 2.5)
        sh.text_center(X + 165, X + 185, Y + 19, "Листов", 2.5)
        sh.text_center(X + 135, X + 140, Y + 24, d.litera, 3.5, key="litera")
        sh.text_center(X + 150, X + 165, Y + 24, "1", 3.5, key="sheet")
        sh.text_center(X + 165, X + 185, Y + 24, str(sheets), 3.5, key="sheets")
        sh.text_center(X + 135, X + 185, Y + 34, "ООО «АРМ»", 3.5)
        return
    sh.line(X + 135, Y + 15, X + 135, Y + 55, width=0.7)
    sh.line(X + 65, Y + 40, X + 135, Y + 40, width=0.7)
    lines = d.name.split("\n")
    for i, part in enumerate(lines):
        sh.text(X + 68, Y + 27 + i * 6 - (len(lines) - 1) * 3, part, 5, max_width=65, key="name")
    sh.text_center(X + 65, X + 135, Y + 49.5, material, 3.5, key="material")
    sh.line(X + 135, Y + 20, X + 185, Y + 20)
    sh.line(X + 135, Y + 35, X + 185, Y + 35, width=0.7)
    sh.line(X + 135, Y + 40, X + 185, Y + 40, width=0.7)
    for x in (140, 145, 150, 167):
        sh.line(X + x, Y + 20 if x < 150 else Y + 15, X + x, Y + 35)
    sh.text_center(X + 135, X + 150, Y + 19, "Лит.", 2.5)
    sh.text_center(X + 150, X + 167, Y + 19, "Масса", 2.5)
    sh.text_center(X + 167, X + 185, Y + 19, "Масштаб", 2.5)
    sh.text_center(X + 135, X + 140, Y + 29.5, d.litera, 3.5, key="litera")
    sh.text_center(X + 150, X + 167, Y + 29.5, mass, 3.5, key="mass")
    sh.text_center(X + 167, X + 185, Y + 29.5, scale, 3.5, key="scale")
    sh.line(X + 155, Y + 35, X + 155, Y + 40)
    sh.text(X + 136, Y + 39, "Лист 1", 2.5, key="sheet")
    sh.text(X + 156, Y + 39, f"Листов {sheets}", 2.5, key="sheets")
    sh.text_center(X + 135, X + 185, Y + 49.5, "ООО «АРМ»", 3.5)


def _title_block_2a(sh: Sheet, designation: str, sheet: int) -> None:
    """Title block of the later sheets, form 2a (185×15): the designation and the number of the sheet."""
    X, Y = sh.w - 5 - 185, sh.h - 5 - 15
    sh.rect(X + 0, Y, X + 185, Y + 15, width=0.7)
    sh.text_center(X + 65, X + 175, Y + 10, designation, 7)
    sh.line(X + 175, Y, X + 175, Y + 15)
    sh.text(X + 176, Y + 4, "Лист", 2.5)
    sh.text_center(X + 175, X + 185, Y + 12, str(sheet), 3.5)


def _sketch(sh: Sheet, kind: str, annotations: list[str], keys: str = "annotation") -> None:
    cx, cy = (20 + sh.w - 5) / 2 - (40 if sh.w > 300 else 0), 95 if sh.w > 300 else 90
    if kind == "shaft":
        steps = [(-110, -80, 14), (-80, -20, 25), (-20, 30, 28), (30, 90, 25), (90, 110, 22.5)]
        for x0, x1, r in steps:
            sh.rect(cx + x0, cy - r, cx + x1, cy + r, width=0.5)
        sh.line(cx - 120, cy, cx + 120, cy, width=0.18)
    elif kind == "gear":
        sh.circle(cx - 45, cy, 48, dashes=(4, 2))
        sh.circle(cx - 45, cy, 52)
        sh.circle(cx - 45, cy, 25)
        sh.rect(cx + 30, cy - 52, cx + 70, cy + 52, width=0.5)
        sh.rect(cx + 30, cy - 25, cx + 70, cy + 25, width=0.25)
    elif kind == "housing":
        sh.rect(cx - 100, cy - 60, cx + 60, cy + 60, width=0.5)
        sh.rect(cx - 85, cy - 45, cx + 45, cy + 45, width=0.5)
        sh.circle(cx - 20, cy, 30)
        sh.line(cx - 110, cy, cx + 70, cy, width=0.18)
    elif kind == "nut":
        sh.circle(cx, cy - 10, 35)
        sh.circle(cx, cy - 10, 24)
        sh.circle(cx, cy - 10, 22, dashes=(2, 1))
    elif kind == "bracket":
        sh.rect(cx - 90, cy - 10, cx + 50, cy + 5, width=0.5)
        sh.rect(cx - 90, cy - 60, cx - 75, cy + 5, width=0.5)
        sh.circle(cx + 30, cy - 2, 9)
    elif kind == "assembly":
        sh.rect(cx - 110, cy - 55, cx + 70, cy + 55, width=0.5)
        sh.circle(cx - 20, cy, 40)
        sh.circle(cx - 20, cy, 15)
        sh.rect(cx - 140, cy - 12, cx + 100, cy + 12, width=0.3)
    else:  # disc
        sh.circle(cx - 40, cy, 45)
        sh.circle(cx - 40, cy, 20)
        sh.rect(cx + 30, cy - 45, cx + 50, cy + 45, width=0.5)
        sh.rect(cx + 30, cy - 20, cx + 50, cy + 20, width=0.25)
    # dimensions and symbols around the view
    slots = [(-95, -70), (-10, -72), (60, -60), (-100, 75), (0, 78), (60, 70), (85, -10), (-115, 0)]
    if sh.w < 300:
        slots = [(-75, -65), (5, -68), (-75, 68), (5, 72), (55, -30), (55, 30), (-80, 0), (40, 0)]
    for i, ((dx, dy), ann) in enumerate(zip(slots, annotations, strict=False)):  # at most len(slots) annotations
        if ann.isdigit() and len(ann) == 1:  # item number on an assembly drawing
            sh.circle(cx + dx + 1.8, cy + dy - 1.8, 4, width=0.25)
        sh.text(cx + dx, cy + dy, ann, 5 if ann.isdigit() and len(ann) == 1 else 3.5, key=f"{keys}:{i}")
        sh.line(cx + dx, cy + dy + 1, cx + dx + max(12, sh.width(ann)), cy + dy + 1, width=0.18)


def _variant_table(sh: Sheet, variants: list[Variant]) -> None:
    """The table of variants of a group drawing, GOST 2.113, 4.1, in the top right of the field: the designation of
    each variant, in full on its first row and as «-01» on the next ones (4.1.12), its variable dimensions and its
    mass (4.1.10). Values are keyed «variant:<row>:designation», «…:mass» and «…:<column>»."""
    columns = [("Обозначение", 46.0), *((name, 18.0) for name in variants[0].data), ("Масса, кг", 22.0)]
    width = sum(w for _, w in columns)
    x0, y0 = sh.w - 13 - width, 26.0
    height = 8.0 * (1 + len(variants))
    sh.rect(x0, y0, x0 + width, y0 + height, width=0.5)
    for r in range(1, 1 + len(variants)):
        sh.line(x0, y0 + 8 * r, x0 + width, y0 + 8 * r, width=0.5 if r == 1 else 0.25)
    x = x0
    for i, (label, w) in enumerate(columns):
        if i:
            sh.line(x, y0, x, y0 + height, width=0.5)
        sh.text_center(x, x + w, y0 + 5.5, label, 3.5)
        x += w
    for row, variant in enumerate(variants):
        yb, key = y0 + 8 * (row + 2) - 2.2, f"variant:{row}:"
        sh.shortened(x0 + 1.5, yb, variant.designation, variant.written, columns[0][1] - 3, key=key + "designation")
        x = x0 + columns[0][1]
        for (name, w), value in zip(columns[1:-1], variant.data.values(), strict=True):
            sh.text_center(x, x + w, yb, value, 3.5, key=key + name)
            x += w
        sh.text_center(x, x + columns[-1][1], yb, variant.mass, 3.5, key=key + "mass")


def draw_drawing(d: Drawing, password: str | None = None, boxes: dict[str, Box] | None = None) -> bytes:
    """The drawing ``d``; with ``boxes`` also where its values are drawn, keyed as ``Drawing.param_keys`` says."""
    c = new_canvas(password, d.font_type)
    sh = Sheet(c, d.fmt, boxes=boxes, font=d.font_type)
    _frame(sh, d.archive, d.first_use)
    _title_block(sh, d, material=d.material, mass=d.mass, scale=d.scale, sheets=d.sheets)
    _sketch(sh, d.sketch, d.annotations)
    if d.variants:
        _variant_table(sh, d.variants)
    if d.general_roughness:
        sh.text(sh.w - 45, 16, f"√{d.general_roughness} (√)", 5, key="general_roughness")
    # technical requirements above the title block; an item of several lines goes on from the left edge
    X = sh.w - 5 - 185
    lines = [(i, n, part) for i, req in enumerate(d.requirements) for n, part in enumerate(req.split("\n"))]
    y = sh.h - 5 - 55 - 6 - 6 * len(lines)
    for row, (i, n, part) in enumerate(lines):
        sh.text(X, y + row * 6, f"{i + 1}. {part}" if n == 0 else part, 3.5, max_width=185, key=f"requirement:{i}")
    for number, sheet_notes in enumerate(d.more_sheets[: len(d.more_sheets) - d.missing_sheets], start=2):
        c.showPage()
        sh = Sheet(c, d.fmt, page=number, boxes=boxes, font=d.font_type)
        _frame(sh, "", "", top=False)
        _title_block_2a(sh, d.designation, number)
        _sketch(sh, "disc" if d.sketch == "assembly" else d.sketch, sheet_notes, keys=f"sheet{number}:annotation")
    return c.getpdfdata()


NAME_WIDTH = 61  # mm of the «Наименование» column of a specification that text may take
GROUP_NAME_WIDTH = 41  # the same in form 5 of GOST 2.113


def wrap(text: str, width: float = NAME_WIDTH, size: float = 3.5, font: FontType = "A") -> str:
    """``text`` with line breaks between words, so that each line fits ``width`` mm in the drafting lettering."""
    lines: list[str] = []
    for word in text.split():
        if lines and text_width(f"{lines[-1]} {word}", size, font) <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return "\n".join(lines)


SPEC_COLS = [
    (0, 6, "Формат"),
    (6, 12, "Зона"),
    (12, 20, "Поз."),
    (20, 90, "Обозначение"),
    (90, 153, "Наименование"),
    (153, 163, "Кол."),
    (163, 185, "Приме-\nчание"),
]
# Form 5 of GOST 2.113: a group specification by variant B of at most three variants, a column of quantities for
# each, the base variant under «–».
GROUP_COLS = [
    (0, 6, "Формат"),
    (6, 12, "Зона"),
    (12, 20, "Поз."),
    (20, 90, "Обозначение"),
    (90, 133, "Наимено-\nвание"),
    (133, 163, ""),
    (163, 185, "Приме-\nчание"),
]
VARIABLE_DATA = "Переменные данные для исполнений:"  # GOST 2.113, 5.5.1


Line = tuple[str, Row | None, int, str]  # what a line holds, the row, its index in the specification, the text
BLANK: Line = ("blank", None, -1, "")


def _lines(s: Spec) -> list[list[Line]]:
    """The lines of a specification in groups that stay on one page: a heading, a row with the lines of its name.

    Sections start with their heading. In variant A of GOST 2.113 the variable data follow the constant ones under
    a heading of their own, and the data of each variant under its designation, with sections of their own; these
    headings stay on the page of the first row below them.
    """
    groups: list[list[Line]] = []
    section: str | None = None
    variant = ""
    for index, spec_row in enumerate(s.rows):
        keep: list[Line] = []
        if spec_row.variant != variant:
            if not variant:
                keep = [BLANK, ("heading", None, -1, VARIABLE_DATA)]
            keep += [BLANK, ("heading", spec_row, -1, spec_row.variant), BLANK]
            variant, section = spec_row.variant, None
        if spec_row.section != section:
            if section is not None:
                groups.append([BLANK])
            if keep:
                keep += [("section", spec_row, -1, ""), BLANK]
            else:
                groups += [[("section", spec_row, -1, "")], [BLANK]]
            section = spec_row.section
        first, *more = spec_row.name.split("\n")
        groups.append([*keep, ("row", spec_row, index, first), *(("more", spec_row, index, part) for part in more)])
    return groups


def _spec_header(sh: Sheet, s: Spec, X: float, bottom: float) -> None:
    sh.line(X, 20, X + 185, 20, width=0.7)
    for x0, x1, label in GROUP_COLS if s.group == "B" else SPEC_COLS:
        sh.line(X + x0, 5, X + x0, bottom, width=0.7)
        parts = label.split("\n")
        if x1 - x0 <= 8:  # narrow columns get vertical labels
            sh.text(X + x0 + 4, 18, label, 2.5, rotate=90)
        else:
            for i, part in enumerate(parts):
                sh.text_center(X + x0, X + x1, 12 + i * 4 - (len(parts) - 1) * 2, part, 2.5)
    if s.group == "B":  # «Кол. на исполн.» with the base designation and a dash, then the number of each variant
        sh.line(X + 133, 12.5, X + 163, 12.5, width=0.7)
        for k in (1, 2):
            sh.line(X + 133 + 10 * k, 12.5, X + 133 + 10 * k, bottom, width=0.7)
        sh.text_center(X + 133, X + 163, 8, "Кол. на исполн.", 2.5)
        sh.text_center(X + 133, X + 163, 11.5, f"{s.designation} –", 2.5)
        for k, variant in enumerate(s.variants):  # «–» for the base one, then «01», «02»
            number = variant.removeprefix(f"{s.designation}-") if k else "–"
            sh.text_center(X + 133 + 10 * k, X + 143 + 10 * k, 18, number, 3.5)


def _heading(sh: Sheet, X: float, y: float, text: str, right: float, key: str | None = None) -> None:
    """A heading in the «Наименование» column, underlined; a long one starts in the column before."""
    width = min(sh.width(text), right - X - 21)
    x = min(X + 90 + (right - X - 90 - width) / 2, right - 0.8 - width)
    sh.text(x, y - 2, text, 3.5, max_width=right - 0.8 - x, key=key)
    sh.line(x, y - 1.2, x + width, y - 1.2, width=0.25)


def draw_spec(s: Spec, boxes: dict[str, Box] | None = None) -> bytes:
    """The specification ``s``; with ``boxes`` also where its values are drawn: the title block by field, the rows
    as «row:<index in s.rows>:<column>», the headings of the variants as «variant:<designation>». A name with line
    breaks takes several lines, which stay on one page.

    A group specification of GOST 2.113 by variant A takes form 1 of GOST 2.106 with the variable data under their
    headings; by variant B it takes form 5, the quantity of each variant in its column as «row:<index>:qty:<variant>».
    """
    c = new_canvas(font=s.font_type)
    pages: list[list[Line]] = [[]]
    capacity = 23  # lines on the first sheet, 29 on the next ones
    for group in _lines(s):
        if len(pages[-1]) + len(group) > capacity:
            pages.append([])
            capacity = 29
        pages[-1].extend(group)
    name_right = 133 if s.group == "B" else 153
    name_width = GROUP_NAME_WIDTH if s.group == "B" else NAME_WIDTH
    for page_no, page_lines in enumerate(pages, start=1):
        sh = Sheet(c, "A4", page=page_no, boxes=boxes, font=s.font_type)
        _frame(sh, s.archive, s.first_use)
        X = 20
        tb_height = 40 if page_no == 1 else 15
        _spec_header(sh, s, X, sh.h - 5 - tb_height)
        y = 20
        for kind, row, index, name in page_lines:
            y += 8
            sh.line(X, y, X + 185, y, width=0.18)
            key = f"row:{index}:"
            if kind == "section" and row:
                sh.text_center(X + 90, X + name_right, y - 2, row.section, 3.5)
                width = min(sh.width(row.section), name_right - 90 - 1.6)  # under the text, squeezed to the column
                mid = X + (90 + name_right) / 2
                sh.line(mid - width / 2, y - 1.2, mid + width / 2, y - 1.2, width=0.25)
            elif kind == "heading":
                _heading(sh, X, y, name, X + name_right, key=f"variant:{row.variant}" if row else None)
            elif kind == "row" and row:
                sh.text_center(X + 0, X + 6, y - 2, row.fmt, 2.5, key=key + "fmt")
                sh.text_center(X + 12, X + 20, y - 2, row.pos, 3.5, key=key + "pos")
                sh.shortened(X + 21, y - 2, row.designation, row.written, key=key + "designation")
                sh.text(X + 91, y - 2, name, 3.5, max_width=name_width, key=key + "name")
                if s.group == "B":
                    for k, variant in enumerate(s.variants):
                        value = row.quantities.get(variant, "")
                        sh.text_center(X + 133 + 10 * k, X + 143 + 10 * k, y - 2, value, 3.5, key=f"{key}qty:{variant}")
                else:
                    sh.text_center(X + 153, X + 163, y - 2, row.qty, 3.5, key=key + "qty")
                sh.text(X + 164, y - 2, row.note, 3.5, max_width=20, key=key + "note")
            elif kind == "more":
                sh.text(X + 91, y - 2, name, 3.5, max_width=name_width, key=key + "name")
        if page_no == 1:
            _title_block(sh, s, spec_form=True, sheets=len(pages))
        else:
            _title_block_2a(sh, s.designation, page_no)
        c.showPage()
    return c.getpdfdata()


def draw_foreign(root: str, name: str) -> bytes:
    """A document of another kind in the kit: the acceptance certificate of the product ``root``."""
    c = new_canvas()
    sh = Sheet(c, "A4")
    body = [
        ("АКТ", 6),
        ("приёмки комплекта конструкторской документации", 4),
        ("", 4),
        ("г. Калуга                                                    12.06.2025", 3.5),
        ("", 4),
        ("Комиссия в составе представителей КБ и производства приняла комплект КД", 3.5),
        (f"на изделие {root} «{name}» для запуска в опытное", 3.5),
        ("производство. Комплект передан в электронном виде.", 3.5),
        ("", 4),
        ("Замечания: отсутствуют.", 3.5),
        ("", 4),
        ("Председатель комиссии ____________ / Кузнецов А. В.", 3.5),
        ("Члены комиссии          ____________ / Петров С. Н.", 3.5),
    ]
    y: float = 30
    for line, size in body:
        sh.text(25, y, line, size)
        y += size * 2.2
    return c.getpdfdata()
