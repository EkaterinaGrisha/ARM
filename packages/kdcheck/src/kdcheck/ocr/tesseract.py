"""Optional scan recognition with Tesseract: ``kdcheck[ocr]`` plus ``tesseract-ocr`` with the languages of ``ocr`` in
``models.yaml``.

Without them scans are processed without text: the designation comes from the file name and the document gets
a warning. ``ocr.enabled: false``, ``kdcheck run --no-ocr`` or ``ARM_OCR=0`` turn OCR off.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable

from PIL import Image

from kdcheck.config import OcrEngine, TextLayout
from kdcheck.ingest.text import Line, Word, split_wide_lines

log = logging.getLogger(__name__)


@functools.cache
def available(languages: str) -> bool:
    """Whether Tesseract is installed with every language of ``languages``, such as ``rus+eng``."""
    try:
        import pytesseract

        installed = set(pytesseract.get_languages(config=""))
    except Exception:
        return False
    missing = [lang for lang in languages.split("+") if lang not in installed]
    if missing:
        log.warning("Tesseract найден, но нет языков %s — OCR отключён", ", ".join(missing))
        return False
    return True


@functools.cache
def version() -> str:
    import pytesseract

    return str(pytesseract.get_tesseract_version())


def recognize(
    image: Image.Image,
    engine: OcrEngine,
    layout: TextLayout,
    scale: float = 1.0,
    origin: tuple[float, float] = (0.0, 0.0),
) -> list[Line]:
    """Recognize an image placed on a page: page = ``origin`` + pixels × ``scale``."""

    def place(x: float, y: float) -> tuple[float, float]:
        return origin[0] + x * scale, origin[1] + y * scale

    lines = words(image, engine.languages, engine.page_segmentation, engine.min_word_confidence, place)
    return split_wide_lines(lines, layout.cell_gap)


def words(
    image: Image.Image,
    languages: str,
    page_segmentation: int,
    min_confidence: float,
    place: Callable[[float, float], tuple[float, float]],
    characters: str | None = None,
) -> list[Line]:
    """The lines of words Tesseract reads on ``image``, of ``characters`` only when given; ``place`` takes a pixel to
    the page."""
    import pytesseract

    config = f"--psm {page_segmentation}"
    if characters:
        config += f" -c tessedit_char_whitelist={characters}"
    data = pytesseract.image_to_data(
        image.convert("L"), lang=languages, config=config, output_type=pytesseract.Output.DICT
    )
    grouped: dict[tuple[int, int, int], list[tuple[Word, float]]] = {}
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        conf = float(data["conf"][i])
        if not text or conf < min_confidence:
            continue
        x, y, w, h = (data[k][i] for k in ("left", "top", "width", "height"))
        corners = [place(cx, cy) for cx in (x, x + w) for cy in (y, y + h)]
        bbox = (
            min(c[0] for c in corners),
            min(c[1] for c in corners),
            max(c[0] for c in corners),
            max(c[1] for c in corners),
        )
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append((Word(text, bbox), conf / 100))  # Tesseract gives 0–100
    lines: list[Line] = []
    for items in grouped.values():
        found = sorted((w for w, _ in items), key=lambda w: w.bbox[0])
        confidence = round(sum(c for _, c in items) / len(items), 2)
        bbox = (
            min(w.bbox[0] for w in found),
            min(w.bbox[1] for w in found),
            max(w.bbox[2] for w in found),
            max(w.bbox[3] for w in found),
        )
        lines.append(
            Line(
                text=" ".join(w.text for w in found),
                bbox=bbox,
                size=bbox[3] - bbox[1],
                words=found,
                confidence=confidence,
            )
        )
    return lines
