"""Разбор основной надписи (ГОСТ 2.104): форма 1 — чертежи, форма 2 — спецификации.

Положение надписи определяется по подписям граф («Лит.», «Масса», «Масштаб», «Листов»),
масштаб — по расстоянию между ними. Дальше значения берутся из ячеек по геометрии формы,
поэтому разбор одинаково работает для векторных PDF и распознанных сканов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import Extracted, TitleBlock
from .designations import find_designations, normalize_litera
from .document import Line, LoadedDocument, Page, lines_confidence

# центры подписей граф относительно левого верхнего угла надписи, мм
FORM1_ANCHORS = {"лит": (142.5, 17.5), "масса": (158.5, 17.5), "масштаб": (176.0, 17.5)}
FORM2_ANCHORS = {"лит": (142.5, 17.5), "лист": (157.5, 17.5), "листов": (175.0, 17.5)}

FORM1_CELLS = {
    "designation": (65, 0, 185, 15),
    "name": (65, 15, 135, 40),
    "material": (65, 40, 135, 55),
    "litera": (135, 20, 150, 35),
    "mass": (150, 20, 167, 35),
    "scale": (167, 20, 185, 35),
    "sheet": (135, 35, 155, 40),
    "sheets": (155, 35, 185, 40),
}
FORM2_CELLS = {
    "designation": (65, 0, 185, 15),
    "name": (65, 15, 135, 40),
    "litera": (135, 20, 150, 25),
    "sheet": (150, 20, 165, 25),
    "sheets": (165, 20, 185, 25),
}


@dataclass
class Geometry:
    form: int  # 1 — чертёж, 2 — спецификация
    x0: float
    y0: float
    s: float  # единиц страницы на мм
    anchored: bool

    @property
    def height(self) -> float:
        return 55 if self.form == 1 else 40

    def rect(self, cell: tuple[float, float, float, float], pad: float = 0.8):
        x0, y0, x1, y1 = cell
        return (
            self.x0 + (x0 - pad) * self.s,
            self.y0 + (y0 - pad) * self.s,
            self.x0 + (x1 + pad) * self.s,
            self.y0 + (y1 + pad) * self.s,
        )

    @property
    def bbox(self):
        return self.rect((0, 0, 185, self.height), pad=0)


def _key(word: str) -> str:
    return re.sub(r"[^а-яё]", "", word.lower())


def locate(page: Page) -> Geometry | None:
    """Находит основную надпись на странице; None, если подписей граф нет."""
    found: dict[str, list[tuple[float, float]]] = {}
    for line in page.lines:
        if line.vertical:
            continue
        for w in line.words:
            k = _key(w.text)
            if (
                k in ("лит", "масса", "масштаб", "лист", "листов")
                and w.cx > page.width * 0.4
                and w.cy > page.height * 0.5
            ):
                found.setdefault(k, []).append((w.cx, w.cy))
    for form, anchors in ((1, FORM1_ANCHORS), (2, FORM2_ANCHORS)):
        hits = _best_row(found, anchors, tol=2.5 * page.mm)
        if len(hits) >= 2 or (form == 1 and "масштаб" in hits):
            return _geometry(page, form, anchors, hits)
    return None


def _best_row(found, anchors, tol: float) -> dict[str, tuple[float, float]]:
    """Подписи, лежащие на одной строке (берём строку с наибольшим числом совпадений)."""
    best: dict[str, tuple[float, float]] = {}
    for key in anchors:
        for cx, cy in found.get(key, []):
            row = {key: (cx, cy)}
            for other in anchors:
                if other == key:
                    continue
                for ox, oy in found.get(other, []):
                    if abs(oy - cy) < tol and (ox - cx) * (anchors[other][0] - anchors[key][0]) > 0:
                        row[other] = (ox, oy)
                        break
            if len(row) > len(best):
                best = row
    return best


def _geometry(page: Page, form: int, anchors, hits) -> Geometry:
    s = page.mm
    keys = sorted(hits, key=lambda k: anchors[k][0])
    if len(keys) >= 2:
        a, b = keys[0], keys[-1]
        est = (hits[b][0] - hits[a][0]) / (anchors[b][0] - anchors[a][0])
        if 0.6 * s < est < 1.6 * s:
            s = est
    k = keys[-1]
    x0 = hits[k][0] - anchors[k][0] * s
    y0 = hits[k][1] - anchors[k][1] * s
    return Geometry(form=form, x0=x0, y0=y0, s=s, anchored=True)


def default_geometry(page: Page, form: int = 1) -> Geometry:
    s = page.mm
    height = 55 if form == 1 else 40
    return Geometry(form=form, x0=page.width - (5 + 185) * s, y0=page.height - (5 + height) * s, s=s, anchored=False)


def lines_in(page: Page, rect, *, vertical: bool = False) -> list[Line]:
    x0, y0, x1, y1 = rect
    picked = [ln for ln in page.lines if ln.vertical == vertical and x0 <= ln.cx <= x1 and y0 <= ln.cy <= y1]
    return sorted(picked, key=lambda ln: (round(ln.cy / 4), ln.bbox[0]))


def _union(lines: list[Line]):
    return (
        min(ln.bbox[0] for ln in lines),
        min(ln.bbox[1] for ln in lines),
        max(ln.bbox[2] for ln in lines),
        max(ln.bbox[3] for ln in lines),
    )


def _extracted(doc: LoadedDocument, page: Page, lines: list[Line], value, confidence: float | None = None):
    if not lines or value in (None, ""):
        return None
    raw = " ".join(ln.text for ln in lines)
    confidence = confidence if confidence is not None else lines_confidence(lines)
    return Extracted(
        value=value, raw=raw, confidence=confidence, source=doc.source(page, _union(lines), raw, confidence)
    )


_LABEL_WORDS = {"лит", "масса", "масштаб", "лист", "листов"}


def _cell_text(lines: list[Line]) -> str:
    words = [w.text for ln in lines for w in ln.words if _key(w.text) not in _LABEL_WORDS]
    return " ".join(words).strip()


def parse(doc: LoadedDocument, page: Page, geom: Geometry) -> TitleBlock:
    cells = FORM1_CELLS if geom.form == 1 else FORM2_CELLS
    tb = TitleBlock()
    got: dict[str, list[Line]] = {name: lines_in(page, geom.rect(cell)) for name, cell in cells.items()}

    # обозначение: самая крупная строка с обозначением ЕСКД в графе 2
    des_lines = got["designation"]
    candidates = [(ln, d) for ln in des_lines for d, _, _ in find_designations(ln.text)]
    if not candidates:  # бывает, что обозначение чуть выходит за ячейку
        wide = lines_in(page, geom.rect((40, -6, 185, 18)))
        candidates = [(ln, d) for ln in wide for d, _, _ in find_designations(ln.text)]
    if candidates:
        ln, d = max(candidates, key=lambda c: c[0].size)
        tb.designation = _extracted(doc, page, [ln], d)

    name_lines = [ln for ln in got["name"] if not find_designations(ln.text)]
    name = _cell_text(name_lines)
    name = re.sub(r"\s+", " ", name)
    tb.name = _extracted(doc, page, name_lines, name or None)

    if "material" in got:
        mat = _cell_text(got["material"])
        tb.material = _extracted(doc, page, got["material"], mat or None)
    if "mass" in got:
        m = re.search(r"\d+(?:[.,]\d+)?", _cell_text(got["mass"]))
        tb.mass = _extracted(doc, page, got["mass"], m.group(0) if m else None)
    if "scale" in got:
        m = re.search(r"\d+(?:[.,]\d+)?\s?:\s?\d+(?:[.,]\d+)?", _cell_text(got["scale"]))
        tb.scale = _extracted(doc, page, got["scale"], m.group(0).replace(" ", "") if m else None)

    lit = _cell_text(got["litera"])
    lit_m = re.search(r"[ПЭТОИАБ0OAБ]\s?\d?", lit.upper())
    tb.litera = _extracted(doc, page, got["litera"], normalize_litera(lit_m.group(0)) if lit_m else None)

    for key in ("sheet", "sheets"):
        m = re.search(r"\d+", _cell_text(got[key]))
        setattr(tb, key, _extracted(doc, page, got[key], int(m.group(0)) if m else None))

    tb.revision = _revision(doc, page, geom)
    tb.archive_number = _margin_value(doc, page, ("подл",))
    tb.first_use = _margin_value(doc, page, ("примен", "перв"))
    return tb


def _revision(doc: LoadedDocument, page: Page, geom: Geometry) -> Extracted | None:
    """Номер последнего изменения — наибольший номер в графе «Изм.» над строкой заголовков."""
    header_y = geom.y0 + 22.5 * geom.s
    col = (geom.x0 - 1 * geom.s, geom.x0 + 7.5 * geom.s)
    for ln in lines_in(page, geom.rect((0, 0, 65, 30))):
        for w in ln.words:
            if _key(w.text) == "изм":
                header_y = w.cy
                col = (w.bbox[0] - 1.5 * geom.s, w.bbox[0] + 7 * geom.s)
                break
    best: tuple[int, Line] | None = None
    for ln in page.lines:
        if ln.vertical or not (geom.y0 - 1 * geom.s <= ln.cy < header_y - 2 * geom.s):
            continue
        for w in ln.words:
            if col[0] <= w.cx <= col[1] and re.fullmatch(r"\d{1,3}", w.text):
                num = int(w.text)
                if best is None or num > best[0]:
                    best = (num, ln)
    if best is None:
        return None
    num, ln = best
    row = [
        x for x in page.lines if not x.vertical and abs(x.cy - ln.cy) < 2 * geom.s and x.bbox[0] < geom.x0 + 66 * geom.s
    ]
    raw = " ".join(x.text for x in sorted(row, key=lambda x: x.bbox[0]))
    return Extracted(value=num, raw=raw, source=doc.source(page, _union(row), raw, lines_confidence(row)))


def _margin_value(doc: LoadedDocument, page: Page, label_keys: tuple[str, ...]) -> Extracted | None:
    """Значение вертикальной графы на левом поле (архивный номер, первое применение)."""
    labels = [ln for ln in page.lines if ln.vertical and all(k in ln.text.lower() for k in label_keys[:1])]
    for label in labels:
        lx0, ly0, lx1, ly1 = label.bbox
        best = None
        for ln in page.lines:
            if not ln.vertical or ln is label:
                continue
            if not (lx1 - 1 <= ln.bbox[0] <= lx1 + 9 * page.mm):
                continue
            overlap = min(ly1, ln.bbox[3]) - max(ly0, ln.bbox[1])
            if overlap > 0.4 * min(ly1 - ly0, ln.bbox[3] - ln.bbox[1]):
                best = ln
                break
        if best is not None:
            value = find_designations(best.text)
            text = value[0][0] if value else best.text.strip()
            return Extracted(value=text, raw=best.text, source=doc.source(page, best.bbox, best.text, best.confidence))
    return None


def has_form1_labels(page: Page) -> bool:
    geom = locate(page)
    return geom is not None and geom.form == 1
