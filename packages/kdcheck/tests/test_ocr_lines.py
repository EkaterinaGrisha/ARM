"""Lines of a drawing read again by PP-OCRv5 and weighed with Tesseract (ADR-0030)."""

import json
import re
from pathlib import Path

import pytest
from kdcheck.config import load_settings
from kdcheck.ingest.document import load_document, page_raster
from kdcheck.ingest.pdf import get_backend
from kdcheck.ingest.text import Line
from kdcheck.layout.raster import Placement
from kdcheck.ocr import lines, ppocr, tesseract
from PIL import Image

SETTINGS = load_settings(use_ocr=True)
KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"
TRUTH = json.loads((KIT.parent / "demo_kit_truth.json").read_text(encoding="utf-8"))["documents"]
SCANS = sorted(p for p, t in TRUTH.items() if t.get("origin") == "scan" and t.get("requirements"))


def test_the_weighed_reading_takes_the_signs_only_tesseract_read():
    assert lines.weigh("Закалить 45...50 HRC.", "Закалить 45.50 HRC") == "Закалить 45...50 HRC."


def test_the_weighed_reading_takes_no_letter_only_tesseract_read():
    assert lines.weigh("Острые кромки притупить", "Острые кромк притупить") == "Острые кромк притупить"
    assert lines.weigh("Размеры для спрабок", "Размеры для справок") == "Размеры для справок"


def test_the_number_of_an_item_comes_from_tesseract_when_ppocr_reads_it_as_a_letter():
    assert lines.weigh("3. Маркиробать обозначение", "В. Маркировать обозначение") == "3. Маркировать обозначение"
    assert lines.weigh("3. Маркиробать", "3. Маркировать") == "3. Маркировать"
    assert lines.weigh("Маркиробать обозначение", "В. Маркировать обозначение") == "В. Маркировать обозначение"


def test_a_digit_between_cyrillic_letters_is_its_letter():
    twins = SETTINGS.dictionaries.letter_lookalikes
    assert lines.letters_for_digits("Подшипник 205 Г0СТ 8338-75", twins) == "Подшипник 205 ГОСТ 8338-75"
    assert lines.letters_for_digits("Б0лт", twins) == "Болт"


def test_a_digit_of_a_grade_or_beside_a_digit_stays():
    twins = SETTINGS.dictionaries.letter_lookalikes
    for text in ("Ст3сп ГОСТ 380-2005", "12Х18Н10Т", "Болт М12х40", "АД0"):
        assert lines.letters_for_digits(text, twins) == text


def test_a_range_read_with_two_dots_gets_three_and_a_number_of_a_standard_keeps_its_dot():
    dots = SETTINGS.dictionaries.range_dots
    assert re.sub(dots, "...", "Закалить 45..50 HRC, h 2…3 мм") == "Закалить 45...50 HRC, h 2...3 мм"
    assert re.sub(dots, "...", "Общие допуски по ГОСТ 30893.1, Ra 3,2") == "Общие допуски по ГОСТ 30893.1, Ra 3,2"


def test_without_the_recognizer_the_lines_stay(monkeypatch):
    monkeypatch.setattr(ppocr, "recognizer", lambda rules: None)
    found = [Line(text="1. Размеры для спрабок", bbox=(10, 10, 100, 14), size=4)]
    image = Image.new("L", (200, 200), 255)
    assert lines.reread(found, image, 1.0, (0.0, 0.0), Placement(150, 150, 1.0), SETTINGS) == found


needs_ocr = pytest.mark.skipif(not tesseract.available(SETTINGS.models.ocr.languages), reason="нужен Tesseract")


@needs_ocr
@pytest.mark.parametrize("path", SCANS)
def test_lines_above_the_title_block_are_read_again_and_those_in_it_stay(ppocr_models, path):
    doc = load_document(KIT / path, KIT, SETTINGS)
    page = doc.pages[0]
    assert page.regions is not None
    block = page.regions.block.placement
    reader = ppocr.recognizer(SETTINGS.models.ocr.ppocr)
    assert reader is not None
    inside = [ln for ln in page.lines if ln.cx >= block.x0 and ln.cy >= block.y0]
    above = [ln for ln in page.lines if not ln.vertical and ln.cy < block.y0]
    assert above and all(ln.engine == (reader.name, reader.version) for ln in above)
    assert all(ln.engine is None for ln in inside)
    image, origin, scale = page_raster(doc, page, SETTINGS, get_backend("pdfium"))
    assert lines.reread(inside, image, scale, origin, block, SETTINGS) == inside
