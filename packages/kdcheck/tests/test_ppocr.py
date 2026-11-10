"""The PP-OCRv5 recognizer of cells (ADR-0024): decoding, lines cut to their ink, words placed on the page, the
fallback to Tesseract, and the real model on the scans of the demo kit when its files are made."""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from kdcheck.config import load_settings
from kdcheck.extract import title_block
from kdcheck.ingest.document import load_document, page_raster
from kdcheck.ingest.pdf import get_backend
from kdcheck.normalize.designations import Designations
from kdcheck.ocr import cells, ppocr, tesseract
from kdcheck.pipeline import BaselinePipeline
from PIL import Image

KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"
TRUTH = json.loads((KIT.parent / "demo_kit_truth.json").read_text(encoding="utf-8"))["documents"]
SCANS = sorted(p for p, t in TRUTH.items() if t.get("origin") == "scan")
ALPHABET = ("", "A", "А", "Б", "1", "x", " ")  # the blank, a Latin A, Cyrillic А and Б, a digit, a Latin x, a space


def steps(*best: int, p: float = 0.9) -> np.ndarray:
    """Probabilities with ``best`` the most probable character of each step."""
    probs = np.full((len(best), len(ALPHABET)), (1 - p) / (len(ALPHABET) - 1), dtype=np.float32)
    for t, k in enumerate(best):
        probs[t, k] = p
    return probs


def test_repeats_and_blanks_are_dropped():
    chars = ppocr.decode(steps(2, 2, 0, 2, 3, 6, 3), ALPHABET)
    assert "".join(c.text for c in chars) == "ААБ Б"
    assert [c.step for c in chars] == [0, 3, 4, 5, 6]


def test_a_cell_reads_its_characters_a_latin_lookalike_counting_for_its_letter():
    probs = steps(1, 5, 4)  # Latin A, a Latin x the cell may not hold, a digit
    probs[0, 2] = 0.05  # the Cyrillic А is less probable than the Latin A, but the two together are the most
    probs[1, 0] = 0.05  # without the x the blank is the most probable
    limit = ppocr.allowed(ALPHABET, "АБ1", (("A", "А"),))
    assert "".join(c.text for c in ppocr.decode(probs, ALPHABET)) == "Ax1"
    assert "".join(c.text for c in ppocr.decode(probs, ALPHABET, limit)) == "А1"


RULES = load_settings().models.ocr.ppocr


def test_lines_are_cut_close_to_their_ink_and_specks_left_out():
    ink = np.zeros((100, 300), dtype=bool)
    ink[10:30, 50:150] = True  # a line 20 px high
    ink[50:70, 40:200] = True  # a second one
    ink[52:55, 280:283] = True  # a speck far right of it
    ink[90:92, 0:300] = True  # a rule too thin to be text
    boxes = ppocr.line_boxes(ink, RULES, low=10, min_ink=50)
    m = round(RULES.margin * 20)
    assert boxes == [(50 - m, 10 - m, 150 + m, 30 + m), (40 - m, 50 - m, 200 + m, 70 + m)]


def test_two_lines_a_descender_joins_part_at_the_valley_of_ink():
    ink = np.zeros((80, 200), dtype=bool)
    ink[10:30, 10:190] = True  # a line
    ink[30:38, 20:25] = True  # a descender down to the next line: the rows of ink run on
    ink[38:58, 10:150] = True  # the next line
    ink[60:63, 10:190] = True  # a rule under it, too thin to be text, apart from it
    boxes = ppocr.line_boxes(ink, RULES, low=10, min_ink=10)
    assert len(boxes) == 2
    (_, top1, _, bottom1), (_, top2, _, bottom2) = boxes
    m = round(RULES.margin * 20)
    assert top1 == 10 - m and bottom1 <= 38 + m and top2 >= 30 - round(RULES.margin * 28) and bottom2 < 63 + m


class FakeSession:
    """Spells «АБ Б» along a line, the space at its middle: a step of the answer covers 8 px, as with the model."""

    def __init__(self):
        self.widths = []

    def run(self, output_names, input_feed):
        x = input_feed["x"]
        assert x.shape[:3] == (1, 3, RULES.height) and x.dtype == np.float32
        self.widths.append(x.shape[3])
        n = x.shape[3] // 8
        best = [0] * n
        best[n // 8], best[n // 4], best[n // 2], best[3 * n // 4] = 2, 3, 6, 3
        return [steps(*best)[None]]


def test_a_word_has_the_box_of_its_ink_between_the_spaces():
    session = FakeSession()
    reader = ppocr.Recognizer(session, ALPHABET, "model", "1")
    grey = Image.new("L", (200, 60), 255)
    ink = Image.new("L", (200, 60), 0)
    ink.paste(255, (20, 20, 90, 40))  # two words with paper between them
    ink.paste(255, (110, 20, 180, 40))
    lines = ppocr.read(reader, grey, ink, lambda x, y: (x / 2, y / 2), RULES, low=10, min_ink=10, min_confidence=0)
    assert len(lines) == 1
    line = lines[0]
    assert line.text == "АБ Б" and [w.text for w in line.words] == ["АБ", "Б"]
    assert [w.bbox for w in line.words] == [(10, 10, 45, 20), (55, 10, 90, 20)]  # the ink, on the page at half scale
    assert line.bbox == (10, 10, 90, 20)
    assert line.engine == ("model", "1") and line.words[0].engine == ("model", "1")
    assert line.confidence == pytest.approx(0.9)
    m = round(RULES.margin * 20)  # the line cut to its ink, 160 × 20 px, with the margin, brought to 48 px high
    assert session.widths == [math.ceil(RULES.height * (160 + 2 * m) / (20 + 2 * m))]


def test_without_its_files_the_cells_are_read_by_tesseract(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("ARM_MODELS_DIR", str(tmp_path))
    settings = load_settings(use_ocr=True)
    assert settings.models.ocr.cells.engine == "ppocr"
    assert cells.ppocr_reader(settings) is None
    assert "fetch_ppocr" in caplog.text
    by_tesseract = settings.models.model_copy(
        update={
            "ocr": settings.models.ocr.model_copy(
                update={"cells": settings.models.ocr.cells.model_copy(update={"engine": "tesseract"})}
            )
        }
    )
    assert cells.ppocr_reader(settings.model_copy(update={"models": by_tesseract})) is None


OCR = load_settings(use_ocr=True)
needs_ocr = pytest.mark.skipif(not tesseract.available(OCR.models.ocr.languages), reason="нужен Tesseract")


@pytest.mark.parametrize("path", SCANS)
def test_the_model_reads_the_title_block_of_a_scan(ppocr_models, path):
    """Without Tesseract too: the block is placed by the lines of its form, and its cells are read by the model."""
    reader = cells.ppocr_reader(OCR)
    assert reader is not None
    plain = load_settings(use_ocr=False)
    doc = load_document(KIT / path, KIT, plain)
    page = doc.pages[0]
    image, origin, scale = page_raster(doc, page, plain, get_backend("pdfium"))
    regions = cells.find(image, scale, origin, page.mm / scale, OCR)
    assert regions is not None
    lines = regions.cells["designation"]
    eskd = Designations(OCR.dictionaries)
    assert title_block.cell_value("designation", " ".join(ln.text for ln in lines), eskd) == TRUTH[path]["designation"]
    assert all(ln.engine == (reader.name, reader.version) for ln in lines)


@needs_ocr
def test_the_report_names_the_recognizer_of_the_cells(ppocr_models):
    engines = {e.name for e in BaselinePipeline(OCR).engines()}
    assert {"tesseract", OCR.models.ocr.ppocr.model} <= engines
