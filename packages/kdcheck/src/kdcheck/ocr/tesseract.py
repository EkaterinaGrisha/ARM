"""Optional scan recognition with Tesseract: ``kdcheck[ocr]`` plus ``tesseract-ocr`` with the ``rus`` language.

Without them scans are processed without text: the designation comes from the file name and the document gets
a warning. ``ARM_OCR=0`` turns OCR off.
"""

from __future__ import annotations

import functools
import logging
import os

from PIL import Image

from kdcheck.ingest.text import Line, Word, split_wide_lines

log = logging.getLogger(__name__)


@functools.cache
def available() -> bool:
    if os.environ.get("ARM_OCR", "1") == "0":
        return False
    try:
        import pytesseract

        langs = set(pytesseract.get_languages(config=""))
    except Exception:
        return False
    if "rus" not in langs:
        log.warning("Tesseract найден, но нет языка rus — OCR отключён")
        return False
    return True


def recognize(image: Image.Image, scale: float = 1.0, origin: tuple[float, float] = (0.0, 0.0)) -> list[Line]:
    """Recognize an image placed on a page: page = ``origin`` + pixels × ``scale``."""
    import pytesseract

    gray = image.convert("L")
    data = pytesseract.image_to_data(gray, lang="rus+eng", config="--psm 11", output_type=pytesseract.Output.DICT)
    grouped: dict[tuple[int, int, int], list[tuple[Word, float]]] = {}
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        conf = float(data["conf"][i])
        if not text or conf < 30:
            continue
        x, y, w, h = (data[k][i] * scale for k in ("left", "top", "width", "height"))
        x, y = x + origin[0], y + origin[1]
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append((Word(text, (x, y, x + w, y + h)), conf / 100))
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
    return split_wide_lines(lines)
