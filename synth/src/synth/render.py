"""Sheets of ESKD documents drawn with reportlab (BSD): the frame, the title block, specifications and drawings.

Pages are drawn in the bundled DejaVu Sans with no timestamps, so the same document gives the same bytes on any
machine. Coordinates are millimetres from the top left corner of the sheet.
"""

from __future__ import annotations

import io
from itertools import pairwise
from pathlib import Path

from reportlab import rl_config
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from synth.model import Drawing, Row, Spec

MM = 72 / 25.4
FORMATS = {"A4": (210, 297), "A3": (420, 297)}

FONT = "DejaVuSans"
# DejaVu Sans 2.37 from https://github.com/dejavu-fonts/dejavu-fonts/releases, license in fonts/LICENSE
FONT_FILE = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"


def new_canvas(password: str | None = None) -> Canvas:
    """A canvas that writes the same bytes for the same drawing: no timestamps, a fixed document ID.

    With ``password`` the PDF opens only with it.
    """
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(FONT_FILE)))
    rl_config.useA85 = 0  # binary streams as is, as CAD systems and scanners write them, not a quarter larger
    # The initial font of each page, else every page refers to an unused Helvetica. The stub types it as a float.
    return Canvas(
        io.BytesIO(),
        invariant=1,
        pageCompression=1,
        initialFontName=FONT,  # type: ignore[arg-type]
        encrypt=password,
    )


class Sheet:
    """A page with coordinates in millimetres from the top left corner."""

    CONDENSE = 0.85  # narrow lettering, like the drafting font of GOST 2.304

    def __init__(self, canvas: Canvas, fmt: str) -> None:
        self.w, self.h = FORMATS[fmt]
        self.c = canvas
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
        return pdfmetrics.stringWidth(s, FONT, size * MM * 1.35) * self.CONDENSE / MM

    def text(
        self, x: float, y: float, s: str, size: float = 3.5, rotate: int = 0, max_width: float | None = None
    ) -> None:
        """Text with the baseline starting at (x, y); ``size`` is the height of capitals in mm.

        Rotated text runs bottom to top, as in the columns of the left margin.
        """
        if not s:
            return
        fontsize = size * MM * 1.35
        c = self.c
        c.saveState()
        c.translate(*self._pt(x, y))
        if rotate:
            c.rotate(rotate)
        else:
            sx = self.CONDENSE
            if max_width:
                natural = self.width(s, size)
                if natural > max_width:
                    k = max_width / natural
                    sx *= max(k, 0.75)
                    if k < 0.75:
                        fontsize *= k / 0.75
            c.scale(sx, 1)
        c.setFont(FONT, fontsize)
        c.drawString(0, 0, s)
        c.restoreState()

    def text_center(self, x0: float, x1: float, y_base: float, s: str, size: float = 3.5) -> None:
        if not s:
            return
        avail = x1 - x0 - 1.6
        width = min(self.width(s, size), avail)
        self.text(x0 + (x1 - x0 - width) / 2, y_base, s, size, max_width=avail)


def _frame(sh: Sheet, archive: str, first_use: str) -> None:
    sh.rect(20, 5, sh.w - 5, sh.h - 5, width=0.7)
    # columns 19–23 (left margin, bottom) and 24–25 (left margin, top)
    x0, x1, x2 = 8, 13, 20
    labels = [
        ("Инв. № подл.", archive, 25),
        ("Подп. и дата", "", 35),
        ("Взам. инв. №", "", 25),
        ("Инв. № дубл.", "", 25),
        ("Подп. и дата", "", 35),
    ]
    y = sh.h - 5
    for label, value, height in labels:
        sh.rect(x0, y - height, x1, y)
        sh.rect(x1, y - height, x2, y)
        sh.text(x0 + 3.6, y - 2, label, 2.5, rotate=90)
        sh.text(x1 + 5, y - 2, value, 3.5, rotate=90)
        y -= height
    top = [("Перв. примен.", first_use, 60), ("Справ. №", "", 60)]
    y = 5
    for label, value, height in top:
        sh.rect(x0, y, x1, y + height)
        sh.rect(x1, y, x2, y + height)
        sh.text(x0 + 3.6, y + height - 2, label, 2.5, rotate=90)
        sh.text(x1 + 5, y + height - 2, value, 3.5, rotate=90)
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
        sh.text_center(X + cols[0], X + cols[1], yb, str(ch.number), 2.5)
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
    sh.text_center(X + 65, X + 185, Y + 11, d.designation, 7)
    if spec_form:
        sh.line(X + 135, Y + 15, X + 135, Y + 40, width=0.7)
        sh.text(X + 68, Y + 25, d.name, 5, max_width=65)
        for x in (150, 165):
            sh.line(X + x, Y + 15, X + x, Y + 25)
        sh.line(X + 135, Y + 20, X + 185, Y + 20)
        sh.line(X + 135, Y + 25, X + 185, Y + 25, width=0.7)
        sh.text_center(X + 135, X + 150, Y + 19, "Лит.", 2.5)
        sh.text_center(X + 150, X + 165, Y + 19, "Лист", 2.5)
        sh.text_center(X + 165, X + 185, Y + 19, "Листов", 2.5)
        sh.text_center(X + 135, X + 140, Y + 24, d.litera, 3.5)
        sh.text_center(X + 150, X + 165, Y + 24, "1", 3.5)
        sh.text_center(X + 165, X + 185, Y + 24, str(sheets), 3.5)
        sh.text_center(X + 135, X + 185, Y + 34, "ООО «АРМ»", 3.5)
        return
    sh.line(X + 135, Y + 15, X + 135, Y + 55, width=0.7)
    sh.line(X + 65, Y + 40, X + 135, Y + 40, width=0.7)
    lines = d.name.split("\n")
    for i, part in enumerate(lines):
        sh.text(X + 68, Y + 27 + i * 6 - (len(lines) - 1) * 3, part, 5, max_width=65)
    sh.text_center(X + 65, X + 135, Y + 49.5, material, 3.5)
    sh.line(X + 135, Y + 20, X + 185, Y + 20)
    sh.line(X + 135, Y + 35, X + 185, Y + 35, width=0.7)
    sh.line(X + 135, Y + 40, X + 185, Y + 40, width=0.7)
    for x in (140, 145, 150, 167):
        sh.line(X + x, Y + 20 if x < 150 else Y + 15, X + x, Y + 35)
    sh.text_center(X + 135, X + 150, Y + 19, "Лит.", 2.5)
    sh.text_center(X + 150, X + 167, Y + 19, "Масса", 2.5)
    sh.text_center(X + 167, X + 185, Y + 19, "Масштаб", 2.5)
    sh.text_center(X + 135, X + 140, Y + 29.5, d.litera, 3.5)
    sh.text_center(X + 150, X + 167, Y + 29.5, mass, 3.5)
    sh.text_center(X + 167, X + 185, Y + 29.5, scale, 3.5)
    sh.line(X + 155, Y + 35, X + 155, Y + 40)
    sh.text(X + 136, Y + 39, "Лист 1", 2.5)
    sh.text(X + 156, Y + 39, f"Листов {sheets}", 2.5)
    sh.text_center(X + 135, X + 185, Y + 49.5, "ООО «АРМ»", 3.5)


def _sketch(sh: Sheet, kind: str, annotations: list[str]) -> None:
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
    for (dx, dy), ann in zip(slots, annotations, strict=False):  # draws at most len(slots) annotations
        if ann.isdigit() and len(ann) == 1:  # item number on an assembly drawing
            sh.circle(cx + dx + 1.8, cy + dy - 1.8, 4, width=0.25)
        sh.text(cx + dx, cy + dy, ann, 5 if ann.isdigit() and len(ann) == 1 else 3.5)
        sh.line(cx + dx, cy + dy + 1, cx + dx + max(12, sh.width(ann)), cy + dy + 1, width=0.18)


def draw_drawing(d: Drawing, password: str | None = None) -> bytes:
    c = new_canvas(password)
    sh = Sheet(c, d.fmt)
    _frame(sh, d.archive, d.first_use)
    _title_block(sh, d, material=d.material, mass=d.mass, scale=d.scale)
    _sketch(sh, d.sketch, d.annotations)
    if d.general_roughness:
        sh.text(sh.w - 45, 16, f"√{d.general_roughness} (√)", 5)
    # technical requirements above the title block
    X = sh.w - 5 - 185
    y = sh.h - 5 - 55 - 6 - 6 * len(d.requirements)
    if len(d.requirements) > 0:
        for i, req in enumerate(d.requirements, start=1):
            sh.text(X, y + (i - 1) * 6, f"{i}. {req}", 3.5, max_width=185)
    return c.getpdfdata()


SPEC_COLS = [
    (0, 6, "Формат"),
    (6, 12, "Зона"),
    (12, 20, "Поз."),
    (20, 90, "Обозначение"),
    (90, 153, "Наименование"),
    (153, 163, "Кол."),
    (163, 185, "Приме-\nчание"),
]


def draw_spec(s: Spec) -> bytes:
    c = new_canvas()
    lines: list[tuple[str, Row | None]] = []
    section = None
    for spec_row in s.rows:
        if spec_row.section != section:
            if section is not None:
                lines.append(("blank", None))
            lines.append(("section", spec_row))
            lines.append(("blank", None))
            section = spec_row.section
        lines.append(("row", spec_row))
    first_capacity, next_capacity = 23, 29
    pages = [lines[:first_capacity]]
    rest = lines[first_capacity:]
    while rest:
        pages.append(rest[:next_capacity])
        rest = rest[next_capacity:]
    for page_no, page_lines in enumerate(pages, start=1):
        sh = Sheet(c, "A4")
        _frame(sh, s.archive, s.first_use)
        X = 20
        tb_height = 40 if page_no == 1 else 15
        bottom = sh.h - 5 - tb_height
        sh.line(X, 20, X + 185, 20, width=0.7)
        for x0, x1, label in SPEC_COLS:
            sh.line(X + x0, 5, X + x0, bottom, width=0.7)
            parts = label.split("\n")
            if x1 - x0 <= 8:  # narrow columns get vertical labels
                sh.text(X + x0 + 4, 18, label, 2.5, rotate=90)
            else:
                for i, part in enumerate(parts):
                    sh.text_center(X + x0, X + x1, 12 + i * 4 - (len(parts) - 1) * 2, part, 2.5)
        y = 20
        for kind, row in page_lines:
            y += 8
            sh.line(X, y, X + 185, y, width=0.18)
            if kind == "section" and row:
                sh.text_center(X + 90, X + 153, y - 2, row.section, 3.5)
                width = sh.width(row.section)
                mid = X + 121.5
                sh.line(mid - width / 2, y - 1.2, mid + width / 2, y - 1.2, width=0.25)
            elif kind == "row" and row:
                sh.text_center(X + 0, X + 6, y - 2, row.fmt, 2.5)
                sh.text_center(X + 12, X + 20, y - 2, row.pos, 3.5)
                sh.text(X + 21, y - 2, row.designation, 3.5, max_width=68)
                sh.text(X + 91, y - 2, row.name, 3.5, max_width=61)
                sh.text_center(X + 153, X + 163, y - 2, row.qty, 3.5)
                sh.text(X + 164, y - 2, row.note, 3.5, max_width=20)
        if page_no == 1:
            _title_block(sh, s, spec_form=True, sheets=len(pages))
        else:
            Y = sh.h - 5 - 15
            sh.rect(X + 0, Y, X + 185, Y + 15, width=0.7)
            sh.text_center(X + 65, X + 175, Y + 10, s.designation, 7)
            sh.line(X + 175, Y, X + 175, Y + 15)
            sh.text(X + 176, Y + 4, "Лист", 2.5)
            sh.text_center(X + 175, X + 185, Y + 12, str(page_no), 3.5)
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
