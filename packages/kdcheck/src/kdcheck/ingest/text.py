"""Words and lines of page text with coordinates, shared by the PDF text layer and OCR."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

BBox = tuple[float, float, float, float]


@dataclass
class Word:
    text: str
    bbox: BBox

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Line:
    text: str
    bbox: BBox
    size: float
    vertical: bool = False
    words: list[Word] = field(default_factory=list)
    confidence: float | None = None  # OCR confidence 0..1; None for the text layer

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def lines_confidence(lines: Iterable[Line]) -> float | None:
    """Lowest OCR confidence among the lines; None for the text layer."""
    values = [ln.confidence for ln in lines if ln.confidence is not None]
    return round(min(values), 2) if values else None


def split_wide_lines(lines: list[Line]) -> list[Line]:
    """Split a line at wide horizontal gaps: these are neighbouring table cells, not one phrase."""
    result: list[Line] = []
    for ln in lines:
        if ln.vertical or len(ln.words) < 2:
            result.append(ln)
            continue
        words = sorted(ln.words, key=lambda w: w.bbox[0])
        groups = [[words[0]]]
        for w in words[1:]:
            gap = w.bbox[0] - groups[-1][-1].bbox[2]
            if gap > ln.size * 1.6:
                groups.append([w])
            else:
                groups[-1].append(w)
        if len(groups) == 1:
            result.append(ln)
            continue
        for g in groups:
            bbox = (g[0].bbox[0], min(w.bbox[1] for w in g), g[-1].bbox[2], max(w.bbox[3] for w in g))
            result.append(
                Line(text=" ".join(w.text for w in g), bbox=bbox, size=ln.size, words=g, confidence=ln.confidence)
            )
    return result
