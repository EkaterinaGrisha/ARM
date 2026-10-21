"""Optional scan recognition with Tesseract: ``kdcheck[ocr]`` plus ``tesseract-ocr`` with the languages of ``ocr`` in
``models.yaml``.

Without them scans are processed without text: the designation comes from the file name and the document gets
a warning. ``ocr.enabled: false``, ``kdcheck run --no-ocr`` or ``ARM_OCR=0`` turn OCR off.
"""

from __future__ import annotations

import functools
import logging

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
    import pytesseract

    gray = image.convert("L")
    data = pytesseract.image_to_data(
        gray,
        lang=engine.languages,
        config=f"--psm {engine.page_segmentation}",
        output_type=pytesseract.Output.DICT,
    )
    grouped: dict[tuple[int, int, int], list[tuple[Word, float]]] = {}
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        conf = float(data["conf"][i])
        if not text or conf < engine.min_word_confidence:
            continue
        x, y, w, h = (data[k][i] * scale for k in ("left", "top", "width", "height"))
        x, y = x + origin[0], y + origin[1]
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append((Word(text, (x, y, x + w, y + h)), conf / 100))  # Tesseract gives 0–100
    lines: list[Line] = []
    for items in grouped.values():
        words = sorted((w for w, _ in items), key=lambda w: w.bbox[0])
        confidence = round(sum(c for _, c in items) / len(items), 2)
        bbox = (
            min(w.bbox[0] for w in words),
            min(w.bbox[1] for w in words),
            max(w.bbox[2] for w in words),
            max(w.bbox[3] for w in words),
        )
        lines.append(
            Line(
                text=" ".join(w.text for w in words),
                bbox=bbox,
                size=bbox[3] - bbox[1],
                words=words,
                confidence=confidence,
            )
        )
    return split_wide_lines(lines, layout.cell_gap)
