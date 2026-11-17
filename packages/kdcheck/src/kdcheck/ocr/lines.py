"""Lines of a drawing read again by PP-OCRv5 and weighed with the reading of Tesseract (ADR-0030).

Tesseract finds the lines of a page image outside its tables: the technical requirements and the notes of the field.
PP-OCRv5 reads the same lines right far more often, and the two err differently: Tesseract takes the GOST lettering of
«в» for «б» and loses letters, PP-OCRv5 drops a full stop or two dots of «...» and reads «О» inside a word as «0». So
the weighed reading is that of PP-OCRv5 with the signs only Tesseract read, and then the rules of the dictionaries: a
digit between Cyrillic letters is its letter, a range read with two dots gets three.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from difflib import SequenceMatcher

from PIL import Image

from kdcheck.config import Settings
from kdcheck.ingest.text import Line
from kdcheck.layout.raster import Placement

CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def weigh(tesseract: str, ppocr: str) -> str:
    """The reading of PP-OCRv5 with the signs it left out where Tesseract read them: a sign is neither a letter nor a
    digit."""
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, tesseract, ppocr, autojunk=False).get_opcodes():
        cut = tesseract[i1:i2]
        if tag != "delete":
            out.append(ppocr[j1:j2])
        elif cut.strip() and not any(c.isalnum() for c in cut):
            out.append(cut)
    return "".join(out)


def letters_for_digits(text: str, lookalikes: Mapping[str, str]) -> str:
    """``text`` with a digit of ``lookalikes`` between two Cyrillic letters read as its letter, in the case of the
    letter after it: «Г0СТ» → «ГОСТ», «Б0лт» → «Болт»."""
    chars = list(text)
    for i in range(1, len(chars) - 1):
        letter = lookalikes.get(chars[i])
        before, after = chars[i - 1], chars[i + 1]
        if letter and CYRILLIC.match(before) and CYRILLIC.match(after):
            chars[i] = letter.lower() if after.islower() else letter.upper()
    return "".join(chars)


def reread(
    lines: list[Line],
    image: Image.Image,
    scale: float,
    origin: tuple[float, float],
    block: Placement,
    settings: Settings,
) -> list[Line]:
    """``lines`` of a page image with those outside the title block at ``block`` read again by PP-OCRv5 and weighed;
    page = ``origin`` + pixels × ``scale``. As they were without the files of the recognizer. A line keeps the words
    of Tesseract: their boxes place labels of the form, and its text is what the extractors read."""
    from kdcheck.ocr import ppocr

    rules = settings.models.ocr
    reader = ppocr.recognizer(rules.ppocr)
    if reader is None:
        return lines
    words = settings.dictionaries
    grey = image.convert("L")
    out = []
    for ln in lines:
        if ln.vertical or (ln.cx >= block.x0 and ln.cy >= block.y0):
            out.append(ln)
            continue
        x0, y0, x1, y1 = ln.bbox
        m = rules.lines.margin * (y1 - y0)
        crop = grey.crop(
            (
                (x0 - m - origin[0]) / scale,
                (y0 - m - origin[1]) / scale,
                (x1 + m - origin[0]) / scale,
                (y1 + m - origin[1]) / scale,
            )
        )
        chars = ppocr.decode(reader.probabilities(crop, rules.ppocr.height), reader.alphabet)
        read = "".join(c.text for c in chars).strip()
        if not read:
            out.append(ln)
            continue
        text = letters_for_digits(weigh(ln.text, read), words.letter_lookalikes)
        text = re.sub(words.range_dots, "...", text)
        confidence = round(sum(c.probability for c in chars) / len(chars), 2)
        out.append(dataclasses.replace(ln, text=text, confidence=confidence, engine=(reader.name, reader.version)))
    return out
