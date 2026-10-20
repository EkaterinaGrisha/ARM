"""Разбор спецификации (ГОСТ 2.106, форма 1).

Графы таблицы восстанавливаются по положению заголовков «Обозначение» и «Наименование»
с учётом стандартной ширины граф. Строки собираются по вертикальной координате;
переносы наименования присоединяются к предыдущей позиции, заголовки разделов
(«Детали», «Стандартные изделия»…) задают раздел для последующих строк.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import Extracted, Specification, SpecRow
from . import title_block
from .designations import find_designations, normalize_designation
from .document import LoadedDocument, Page, Word

SECTIONS = [
    "Документация",
    "Комплексы",
    "Сборочные единицы",
    "Детали",
    "Стандартные изделия",
    "Прочие изделия",
    "Материалы",
    "Комплекты",
]
_SECTION_KEYS = {re.sub(r"[^а-я]", "", s.lower()): s for s in SECTIONS}

# графы спецификации: (поле, левая граница, правая граница), мм от левого края таблицы
COLUMNS = [
    ("format", 0, 6),
    ("zone", 6, 12),
    ("position", 12, 20),
    ("designation", 20, 90),
    ("name", 90, 153),
    ("quantity", 153, 163),
    ("note", 163, 185),
]
DESIGNATION_CX, NAME_CX = 55.0, 121.5


@dataclass
class Table:
    x0: float
    s: float
    top: float
    bottom: float

    def column(self, x: float) -> str | None:
        mm = (x - self.x0) / self.s
        for name, left, right in COLUMNS:
            if left <= mm < right:
                return name
        return None


def find_header(page: Page) -> tuple[Word, Word] | None:
    des = [w for ln in page.lines if not ln.vertical for w in ln.words if w.text.lower().startswith("обозначен")]
    names = [w for ln in page.lines if not ln.vertical for w in ln.words if w.text.lower().startswith("наименован")]
    for d in des:
        for n in names:
            if abs(d.cy - n.cy) < 4 * page.mm and n.cx > d.cx and d.cy < page.height * 0.3:
                return d, n
    return None


def is_specification(page: Page) -> bool:
    return find_header(page) is not None


def _table(page: Page, header: tuple[Word, Word] | None, prev: Table | None) -> Table | None:
    if header is None:
        if prev is None:
            return None
        return Table(prev.x0, prev.s, page.height * 0.06, page.height - 22 * prev.s)
    d, n = header
    s = (n.cx - d.cx) / (NAME_CX - DESIGNATION_CX)
    if not 0.6 * page.mm < s < 1.6 * page.mm:
        s = page.mm
    x0 = d.cx - DESIGNATION_CX * s
    top = max(d.bbox[3], n.bbox[3]) + 2 * s
    geom = title_block.locate(page)
    if geom is not None and geom.form == 2:
        bottom = geom.y0 - 0.5 * s
    else:
        bottom = page.height - 21 * s  # форма 2а: надпись высотой 15 мм
    return Table(x0, s, top, bottom)


def parse(doc: LoadedDocument) -> Specification:
    spec = Specification(document_id=doc.id)
    rows: list[SpecRow] = []
    section: str | None = None
    table: Table | None = None
    for page in doc.pages:
        if not page.lines:
            continue
        if spec.designation is None:
            geom = title_block.locate(page)
            if geom is not None and geom.form == 2:
                tb = title_block.parse(doc, page, geom)
                spec.designation, spec.name, spec.litera, spec.revision = (
                    tb.designation,
                    tb.name,
                    tb.litera,
                    tb.revision,
                )
        table = _table(page, find_header(page), table)
        if table is None:
            continue
        section = _parse_page(doc, page, table, rows, section)
    if spec.designation is None:
        spec.designation = _designation_fallback(doc)
    spec.rows = rows
    return spec


def _designation_fallback(doc: LoadedDocument) -> Extracted | None:
    """Обозначение спецификации из крупного текста внизу первого листа или из имени файла."""
    for page in doc.pages[:1]:
        candidates = [
            (ln, d) for ln in page.lines if ln.cy > page.height * 0.8 for d, _, _ in find_designations(ln.text)
        ]
        if candidates:
            ln, d = max(candidates, key=lambda c: c[0].size)
            return Extracted(value=d, raw=ln.text, source=doc.source(page, ln.bbox, ln.text))
    return None


def _cluster_rows(words: list[Word], tol: float) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.cy):
        if rows and abs(rows[-1][-1].cy - w.cy) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def _parse_page(doc: LoadedDocument, page: Page, table: Table, rows: list[SpecRow], section: str | None):
    words = [
        w
        for ln in page.lines
        if not ln.vertical
        for w in ln.words
        if table.top <= w.cy <= table.bottom and table.x0 - 2 * table.s <= w.cx <= table.x0 + 187 * table.s
    ]
    for cluster in _cluster_rows(words, tol=2.2 * table.s):
        cells: dict[str, list[Word]] = {}
        for w in sorted(cluster, key=lambda w: w.bbox[0]):
            col = table.column(w.cx)
            if col:
                cells.setdefault(col, []).append(w)
        if not cells:
            continue
        text = {k: " ".join(w.text for w in v) for k, v in cells.items()}
        only_name = set(cells) == {"name"}
        key = re.sub(r"[^а-я]", "", text.get("name", "").lower())
        if only_name and key in _SECTION_KEYS:
            section = _SECTION_KEYS[key]
            continue
        has_identity = any(text.get(k) for k in ("designation", "position", "quantity", "format"))
        if not has_identity and rows and rows[-1].source and rows[-1].source.page == page.number:
            _append_continuation(doc, page, rows[-1], cells)
            continue
        rows.append(_make_row(doc, page, len(rows) + 1, section, cells))
    return section


def _bbox(words: list[Word]):
    return (
        min(w.bbox[0] for w in words),
        min(w.bbox[1] for w in words),
        max(w.bbox[2] for w in words),
        max(w.bbox[3] for w in words),
    )


def _make_row(
    doc: LoadedDocument, page: Page, index: int, section: str | None, cells: dict[str, list[Word]]
) -> SpecRow:
    row = SpecRow(id=f"{doc.id}-r{index}", section=section)
    all_words = [w for ws in cells.values() for w in ws]
    for field, words in cells.items():
        raw = " ".join(w.text for w in words)
        value: str | float | None = raw
        if field == "designation":
            value = normalize_designation(raw) or raw
        elif field == "quantity":
            value = _quantity(raw)
        elif field == "position":
            m = re.search(r"\d+", raw)
            value = m.group(0) if m else raw
        setattr(row, field, Extracted(value=value, raw=raw, source=doc.source(page, _bbox(words), raw)))
    row.source = doc.source(page, _bbox(all_words), " | ".join(" ".join(w.text for w in ws) for ws in cells.values()))
    return row


def _append_continuation(doc: LoadedDocument, page: Page, row: SpecRow, cells: dict[str, list[Word]]) -> None:
    for field in ("name", "note"):
        words = cells.get(field)
        if not words:
            continue
        add = " ".join(w.text for w in words)
        current: Extracted | None = getattr(row, field)
        if current is None:
            setattr(row, field, Extracted(value=add, raw=add, source=doc.source(page, _bbox(words), add)))
            continue
        current.value = f"{current.value} {add}".strip()
        current.raw = f"{current.raw} {add}".strip()
        if current.source and current.source.bbox:
            x0, y0, x1, y1 = current.source.bbox
            nx0, ny0, nx1, ny1 = page.norm_bbox(_bbox(words))
            current.source.bbox = (min(x0, nx0), min(y0, ny0), max(x1, nx1), max(y1, ny1))


def _quantity(raw: str) -> float | str | None:
    m = re.search(r"\d+(?:[.,]\d+)?", raw)
    if not m:
        return raw or None
    return float(m.group(0).replace(",", "."))
