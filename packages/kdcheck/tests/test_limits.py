"""A time limit on reading one sheet (plan stage 3): a sheet that reads too long ends alone, the others are read."""

import io
import time

import pytest
from kdcheck import limits
from kdcheck.ingest import document
from kdcheck.ocr import tesseract
from PIL import Image, ImageDraw


def test_the_limit_counts_only_inside_a_sheet():
    assert limits.remaining() is None
    with limits.sheet_limit(0):  # 0: no limit
        assert limits.remaining() is None
    with limits.sheet_limit(5):
        left = limits.remaining()
        assert left is not None and 0 < left <= 5
    with limits.sheet_limit(0.01), pytest.raises(limits.SheetTimeout):
        time.sleep(0.02)
        limits.check()
    assert limits.remaining() is None


def _tiff(path, frames=3):
    pages = [Image.new("L", (400, 300), 255) for _ in range(frames)]
    for i, page in enumerate(pages):
        ImageDraw.Draw(page).text((20, 20), f"page {i + 1}", fill=0)
    buf = io.BytesIO()
    pages[0].save(buf, "TIFF", save_all=True, append_images=pages[1:], dpi=(150, 150))
    path.write_bytes(buf.getvalue())
    return path


def test_a_sheet_past_its_limit_costs_only_its_page(tmp_path, settings, monkeypatch):
    quick = settings.model_copy(update={"ingest": settings.ingest.model_copy(update={"page_timeout_sec": 0.05})})

    def slow_second_sheet(page, image, settings, scale, origin):
        page.lines = []
        while page.number == 2:  # as the search for the title block does between its placements
            limits.check()
            time.sleep(0.01)

    monkeypatch.setattr(document, "_use_ocr", lambda settings: True)
    monkeypatch.setattr(document, "unreadable_reason", lambda quality, rules: None)
    monkeypatch.setattr(document, "_ocr", slow_second_sheet)
    doc = document.load_document(_tiff(tmp_path / "scan.tif"), tmp_path, quick)
    assert doc.info.status == "ok"
    assert [p.problem for p in doc.info.pages] == [
        None,
        "лист читался дольше 0.05 с и не дочитан: лимит page_timeout_sec в configs/ingest.yaml",
        None,
    ]


def test_every_sheet_past_its_limit_leaves_the_file_damaged(tmp_path, settings, monkeypatch):
    quick = settings.model_copy(update={"ingest": settings.ingest.model_copy(update={"page_timeout_sec": 0.01})})

    def slow(page, image, settings, scale, origin):
        while True:
            limits.check()
            time.sleep(0.005)

    monkeypatch.setattr(document, "_use_ocr", lambda settings: True)
    monkeypatch.setattr(document, "unreadable_reason", lambda quality, rules: None)
    monkeypatch.setattr(document, "_ocr", slow)
    doc = document.load_document(_tiff(tmp_path / "scan.tif", frames=2), tmp_path, quick)
    assert doc.info.status == "damaged" and "лист читался дольше 0.01 с" in (doc.info.problem or "")


def test_tesseract_gets_the_time_left_and_its_timeout_stops_the_sheet(monkeypatch):
    pytesseract = pytest.importorskip("pytesseract")
    calls = []

    def hung(image, **kwargs):
        calls.append(kwargs["timeout"])
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(pytesseract, "image_to_data", hung)
    image = Image.new("L", (40, 20), 255)
    with limits.sheet_limit(5), pytest.raises(limits.SheetTimeout):
        tesseract.words(image, "rus", 6, 0, lambda x, y: (x, y))
    assert 0 < calls[-1] <= 5
    with pytest.raises(RuntimeError):  # without a limit Tesseract has none, and its own error stays an error
        tesseract.words(image, "rus", 6, 0, lambda x, y: (x, y))
    assert calls[-1] == 0


def test_the_search_for_regions_does_not_swallow_the_limit(tmp_path, settings, monkeypatch):
    from kdcheck.ingest.text import Line, Word
    from kdcheck.ocr import cells

    def out_of_time(*args, **kwargs):
        raise limits.SheetTimeout

    monkeypatch.setattr(document, "_use_ocr", lambda settings: True)
    monkeypatch.setattr(document, "unreadable_reason", lambda quality, rules: None)
    line = Line("АБВГ", (0, 0, 10, 10), 10, words=[Word("АБВГ", (0, 0, 10, 10))])
    monkeypatch.setattr(tesseract, "recognize", lambda *a, **k: [line])
    monkeypatch.setattr(tesseract, "version", lambda: "5")
    monkeypatch.setattr(cells, "find", out_of_time)
    doc = document.load_document(_tiff(tmp_path / "scan.tif", frames=1), tmp_path, settings)
    assert doc.info.status == "damaged" and "лист читался дольше" in (doc.info.problem or "")
