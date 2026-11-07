"""The cache of readings (ADR-0018): a second run gives back what the first read, and nothing else."""

import json
import logging
from pathlib import Path

import pytest
from kdcheck import cache
from kdcheck.ocr import tesseract
from kdcheck.run import run
from PIL import Image

KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"


def _counted(answer: object):
    calls: list[int] = []

    def read():
        calls.append(1)
        return answer

    return read, calls


def test_without_a_folder_every_call_reads_and_nothing_is_kept(tmp_path):
    read, calls = _counted({"text": "a"})
    keys: list[int] = []

    def key():
        keys.append(1)
        return {"x": 1}

    for _ in range(2):
        assert cache.lookup("engine", key, read) == {"text": "a"}
    assert len(calls) == 2 and keys == []  # no key is made without a cache: no pixels hashed
    assert not any(tmp_path.iterdir())


def test_a_second_call_with_the_same_parts_gives_back_the_answer(tmp_path):
    read, calls = _counted({"text": ["Сталь 45"], "conf": [96]})
    with cache.use(tmp_path):
        first = cache.lookup("tesseract", lambda: {"psm": 6, "pixels": "ab"}, read)
        second = cache.lookup("tesseract", lambda: {"pixels": "ab", "psm": 6}, read)  # the order of parts is no matter
        other = cache.lookup("tesseract", lambda: {"psm": 7, "pixels": "ab"}, read)
    assert first == second == other and len(calls) == 2
    records = list((tmp_path / "tesseract").rglob("*.json"))
    assert len(records) == 2 and all(r.parent.name == r.stem[:2] for r in records)
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert set(record) == {"engine", "parts", "answer"} and record["engine"] == "tesseract"
    cache.lookup("tesseract", lambda: {"psm": 6, "pixels": "ab"}, read)  # past the block the cache is off again
    assert len(calls) == 3


def test_an_error_is_not_kept(tmp_path):
    def fail():
        raise RuntimeError("server down")

    read, calls = _counted("ok")
    with cache.use(tmp_path):
        with pytest.raises(RuntimeError):
            cache.lookup("vlm", lambda: {"request": 1}, fail)
        assert cache.lookup("vlm", lambda: {"request": 1}, read) == "ok"
    assert len(calls) == 1


def test_a_broken_record_is_read_again_and_replaced(tmp_path):
    read, calls = _counted("new")
    with cache.use(tmp_path):
        cache.lookup("vlm", lambda: {"request": 2}, lambda: "old")
        (record,) = (tmp_path / "vlm").rglob("*.json")
        record.write_text("{", encoding="utf-8")
        assert cache.lookup("vlm", lambda: {"request": 2}, read) == "new"
        assert cache.lookup("vlm", lambda: {"request": 2}, read) == "new"
    assert len(calls) == 1 and not list(tmp_path.rglob("*.tmp"))


def test_a_cache_that_cannot_be_written_costs_time_not_the_answer(tmp_path, caplog):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    with cache.use(blocked), caplog.at_level(logging.WARNING):
        assert cache.lookup("vlm", lambda: {"request": 3}, lambda: "read") == "read"
    assert "Кэш прочтений не записан" in caplog.text


def test_the_hash_of_pixels_is_the_pixels_and_not_the_file():
    image = Image.new("L", (30, 10), 255)
    image.putpixel((3, 4), 0)
    again = Image.frombytes("L", (30, 10), image.tobytes())
    assert cache.pixels(image) == cache.pixels(again)
    assert cache.pixels(image) != cache.pixels(image.convert("RGB"))
    assert cache.pixels(image) != cache.pixels(Image.frombytes("L", (10, 30), image.tobytes()))


def test_tesseract_reads_a_cell_once(tmp_path, monkeypatch):
    """The threshold and the place on the page apply to the kept answer: they are not in its key."""
    data = {k: [] for k in ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")}
    for k, v in {"text": "МАШН.758422.002", "conf": 91, "left": 2, "top": 3, "width": 40, "height": 9}.items():
        data[k].append(v)
    for k in ("block_num", "par_num", "line_num"):
        data[k].append(1)
    calls: list[str] = []
    monkeypatch.setattr(tesseract, "version", lambda: "5.5.2")
    monkeypatch.setattr(tesseract, "_read", lambda image, languages, config: calls.append(config) or data)
    image = Image.new("RGB", (50, 15), "white")
    with cache.use(tmp_path):
        first = tesseract.words(image, "rus", 6, 0, lambda x, y: (x, y))
        moved = tesseract.words(image, "rus", 6, 95, lambda x, y: (x + 100, y))
        again = tesseract.words(image, "rus", 6, 0, lambda x, y: (x + 100, y))
        tesseract.words(image, "rus", 6, 0, lambda x, y: (x, y), characters="0123456789")
    assert calls == ["--psm 6", "--psm 6 -c tessedit_char_whitelist=0123456789"]
    assert [line.text for line in first] == ["МАШН.758422.002"] and moved == []  # below the threshold of 95
    assert again[0].bbox == (first[0].bbox[0] + 100, *first[0].bbox[1:2], first[0].bbox[2] + 100, first[0].bbox[3])


@pytest.mark.skipif(not tesseract.available("rus+eng"), reason="нужен Tesseract с языками rus+eng")
def test_a_second_run_reads_nothing_again_and_gives_the_same_report(tmp_path, monkeypatch):
    first = run(KIT, kit_id="demo", kit_name="demo", use_ocr=True, cache=tmp_path / "cache")
    read = tesseract._read
    calls: list[int] = []
    monkeypatch.setattr(tesseract, "_read", lambda *a: calls.append(1) or read(*a))
    second = run(KIT, kit_id="demo", kit_name="demo", use_ocr=True, cache=tmp_path / "cache")
    assert calls == [] and any((tmp_path / "cache" / "tesseract").rglob("*.json"))
    drop = {"kit": {"created_at", "processed_at", "duration_sec"}}
    assert first.model_dump(exclude=drop) == second.model_dump(exclude=drop)
