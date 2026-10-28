"""Scans through Augraphy: the same seed gives the same file, the record holds what ran, boxes follow the sheet.

Augraphy comes with the augraphy group of synth on Python 3.11 only (ADR-0013); without it these tests are skipped.
"""

import io
import json
import os
import random
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageStat

pytest.importorskip("augraphy")

from synth.augraphy_scans import capture  # noqa: E402
from synth.model import Drawing, Output, Row, Spec  # noqa: E402
from synth.render import MM, Sheet, draw_drawing, draw_spec, new_canvas  # noqa: E402
from synth.truth import AugraphyCapture, Capture  # noqa: E402

from synth import degrade  # noqa: E402

A4 = (210.0, 297.0)
GEOMETRIC = {"Geometric", "Squish", "PageBorder", "BookBinding", "Folding"}  # effects that move the ink
DRAWING = Drawing(
    "АБВГ.715211.001 Вал.pdf",
    "АБВГ.715211.001",
    "Вал",
    fmt="A4",
    material="Сталь 45 ГОСТ 1050-2013",
    requirements=["Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2."],
    annotations=["Ø40h6", "120"],
    sketch="shaft",
)


def test_the_same_seed_gives_the_same_file():
    pdf = draw_drawing(DRAWING)
    output = Output("scan", "pdf", "poor")
    first, second, other = capture(pdf, output, 5), capture(pdf, output, 5), capture(pdf, output, 6)
    assert first == second
    assert other[0] != first[0]


@pytest.mark.parametrize("quality", ["good", "fair", "poor"])
def test_the_scanner_settings_are_those_synth_degrade_draws(quality):
    """The same document of the same class gets the same resolution, pixels and compression from both degraders."""
    pdf = draw_drawing(DRAWING)
    for seed in range(3):
        _, record = capture(pdf, Output("scan", "jpg", quality), seed)
        planned = degrade.plan(Output("scan", "jpg", quality), [A4], seed, random.Random(seed))
        assert isinstance(record, AugraphyCapture) and record.group == f"augraphy:{quality}"
        assert (record.dpi, record.mode, record.jpeg) == (planned.dpi, planned.mode, planned.jpeg)


def test_a_raster_and_an_unreadable_scan_stay_with_synth_degrade():
    pdf = draw_drawing(DRAWING)
    for output in (Output("raster", "png", "fair"), Output("scan", "jpg", "unreadable")):
        data, record = capture(pdf, output, 4)
        assert isinstance(record, Capture) and (data, record) == degrade.capture(pdf, output, 4)


def test_each_sheet_lists_the_effects_that_ran_with_their_settings():
    pdf = draw_drawing(DRAWING)
    names: set[str] = set()
    for seed in range(8):
        data, record = capture(pdf, Output("scan", "png", "poor"), seed)
        [sheet] = record.sheets
        assert sheet.pixels == Image.open(io.BytesIO(data)).size
        assert (sheet.threshold is not None) == (record.mode == "bilevel")
        for effect in sheet.effects:
            assert effect.phase in ("ink", "paper", "post") and "keypoints" not in effect.settings
            # settings are JSON with no path of the folder the capture ran in
            assert os.sep + "augraphy-" not in json.dumps(effect.settings)
        names |= {effect.name for effect in sheet.effects}
    assert len(names) >= 5


def test_a_capture_leaves_the_working_folder_and_the_generators_as_they_were(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    random.seed(1)
    expected = random.random()
    random.seed(1)
    capture(draw_drawing(DRAWING), Output("scan", "tiff", "poor"), 11)
    assert random.random() == expected
    assert Path.cwd() == tmp_path and not list(tmp_path.iterdir())


def test_a_mark_in_a_box_stays_in_its_box_when_an_effect_moves_the_ink():
    """A black square where the title block is: wherever a geometric effect takes it, the moved box holds its ink."""
    c = new_canvas()
    Sheet(c, "A4")
    x0, y0, x1, y1 = 170, 262, 182, 274  # mm from the top left corner
    c.rect(x0 * MM, (A4[1] - y1) * MM, (x1 - x0) * MM, (y1 - y0) * MM, stroke=0, fill=1)
    pdf = c.getpdfdata()
    sheet_box = (x0 / A4[0], y0 / A4[1], x1 / A4[0], y1 / A4[1])
    moved = set()
    for seed in range(200):
        boxes = {"mark": (1, sheet_box)}
        data, record = capture(pdf, Output("scan", "png", "poor"), seed, boxes)
        ran = {effect.name for effect in record.sheets[0].effects} & GEOMETRIC
        if not ran:
            continue
        image = Image.open(io.BytesIO(data)).convert("L")
        w, h = image.size
        mx0, my0, mx1, my1 = boxes["mark"][1]
        inside = image.crop((round(mx0 * w), round(my0 * h), round(mx1 * w), round(my1 * h)))
        assert ImageStat.Stat(inside).mean[0] < 128, (seed, ran)  # the box is mostly ink
        moved |= ran
        if len(moved) >= 3:
            break
    assert len(moved) >= 3


def test_a_document_of_several_sheets_is_scanned_whole():
    spec = Spec(
        "АБВГ.301411.001 Привод.pdf",
        "АБВГ.301411.001",
        "Привод",
        rows=[Row("Детали", "А4", str(i), f"АБВГ.715211.{i:03d}", "Вал", "1") for i in range(1, 40)],
    )
    pdf = draw_spec(spec)
    sheets = len(pdfium.PdfDocument(pdf))
    assert sheets > 1
    data, record = capture(pdf, Output("scan", "tiff", "poor"), 3)
    assert len(record.sheets) == sheets == getattr(Image.open(io.BytesIO(data)), "n_frames", 1)
    with pytest.raises(ValueError, match="one page"):
        capture(pdf, Output("scan", "jpg", "poor"), 3)
