"""Rasters and scans: the same seed gives the same file, every class keeps to its ranges, boxes follow the sheet."""

import io
import random

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageDraw, ImageStat
from synth.degrade import CLASSES, capture, move, plan
from synth.model import Drawing, Output, Row, Spec
from synth.render import MM, Sheet, draw_drawing, draw_spec, new_canvas
from synth.truth import SheetCapture

A3 = (420.0, 297.0)

DRAWING = Drawing(
    "АБВГ.715211.001 Вал.pdf",
    "АБВГ.715211.001",
    "Вал",
    material="Сталь 45 ГОСТ 1050-2013",
    mass="1,25",
    litera="О1",
    archive="5211-001",
    requirements=["Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2."],
    annotations=["Ø40h6", "120"],
    sketch="shaft",
)
LONG_SPEC = Spec(
    "АБВГ.301411.001 Привод.pdf",
    "АБВГ.301411.001",
    "Привод",
    rows=[Row("Детали", "А4", str(i), f"АБВГ.715211.{i:03d}", "Вал", "1") for i in range(1, 40)],
)


def _pages(data: bytes, container: str) -> list[Image.Image]:
    if container == "pdf":
        pdf = pdfium.PdfDocument(data)
        return [
            next(iter(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]))).get_bitmap(render=False).to_pil()
            for page in pdf
        ]
    image = Image.open(io.BytesIO(data))
    frames = []
    for i in range(getattr(image, "n_frames", 1)):
        image.seek(i)
        frames.append(image.copy())
    return frames


@pytest.mark.parametrize("quality", ["good", "fair", "poor", "unreadable"])
@pytest.mark.parametrize("kind", ["raster", "scan"])
def test_the_same_seed_gives_the_same_file(kind, quality):
    pdf = draw_drawing(DRAWING)
    output = Output(kind, "pdf", quality)
    first, second, other = capture(pdf, output, 5), capture(pdf, output, 5), capture(pdf, output, 6)
    assert first == second
    assert other[0] != first[0]


@pytest.mark.parametrize("quality", ["good", "fair", "poor", "unreadable"])
def test_a_scan_draws_every_parameter_within_its_class(quality):
    r = CLASSES[quality]
    for seed in range(60):
        record = plan(Output("scan", "pdf", quality), [A3, (210.0, 297.0)], seed, random.Random(seed))
        assert record.dpi in r.dpi and record.mode in r.modes
        assert r.ink[0] <= record.ink <= r.ink[1] and r.paper[0] <= record.paper <= r.paper[1]
        assert r.spread[0] <= record.spread <= r.spread[1] and r.fade[0] <= record.fade <= r.fade[1]
        assert r.stains[0] <= record.stains <= r.stains[1]
        assert r.blur[0] <= record.blur <= r.blur[1] and r.noise[0] <= record.noise <= r.noise[1]
        if record.white is not None:
            assert record.mode != "bilevel" and record.white < record.paper
        if record.mode == "bilevel":
            assert record.threshold is not None and record.ink < record.threshold < record.paper - record.stains
        else:
            assert record.threshold is None and r.jpeg[0] <= (record.jpeg or 0) <= r.jpeg[1]
        assert len(record.sheets) == 2
        for page, sheet in enumerate(record.sheets, start=1):
            assert abs(sheet.skew) <= r.skew and max(map(abs, sheet.shift)) <= r.shift
            assert all(s.box.page == page and 0 <= s.box.bbox[0] < s.box.bbox[2] <= 1 for s in sheet.stamps)
        assert not record.sheets[1].folds or r.fold > 0


def test_a_raster_has_resolution_mode_and_compression_only():
    for seed in range(30):
        record = plan(Output("raster", "jpg", "poor"), [A3], seed, random.Random(seed))
        assert record.mode in ("bilevel", "grey") and record.dpi in CLASSES["poor"].dpi
        assert record.jpeg is not None and record.sheets == []
        assert (record.ink, record.paper, record.blur, record.noise) == (0, 255, 0.0, 0.0)


def test_a_box_turns_and_shifts_as_the_image_does():
    """A dot drawn at a known place lands where ``move`` puts the box around it."""
    size, scale = (300, 200), 4  # mm, and pixels per mm
    sheet = SheetCapture(skew=2.5, shift=(3.0, -2.0))
    image = Image.new("L", (size[0] * scale, size[1] * scale), 255)
    for x, y in ((250, 170), (40, 30), (150, 100)):
        ImageDraw.Draw(image).ellipse(((x - 1) * scale, (y - 1) * scale, (x + 1) * scale, (y + 1) * scale), fill=0)
        box = (x / size[0] - 0.003, y / size[1] - 0.005, x / size[0] + 0.003, y / size[1] + 0.005)
        moved = move(box, sheet, size)
        turned = image.rotate(sheet.skew, resample=Image.Resampling.BICUBIC, translate=(12, -8), fillcolor=255)
        inside = turned.crop([round(v * s * scale) for v, s in zip(moved, size * 2, strict=True)])
        assert ImageStat.Stat(inside).extrema[0][0] < 50, (x, y)  # the dot is inside the moved box
        image = Image.new("L", image.size, 255)


def test_capture_moves_every_box_of_the_truth():
    pdf_boxes: dict = {}
    pdf = draw_drawing(DRAWING, boxes=pdf_boxes)
    boxes = dict(pdf_boxes)
    _, record = capture(pdf, Output("scan", "pdf", "poor"), 4, boxes)
    assert record.sheets[0].skew and boxes.keys() == pdf_boxes.keys()
    for key, (page, box) in boxes.items():
        assert box == move(pdf_boxes[key][1], record.sheets[page - 1], A3)


def test_a_mark_in_the_corner_of_a_turned_scan_lies_in_its_moved_box():
    """A black square where the title block is: on a scan turned by two degrees its ink is in the moved box."""
    c = new_canvas()
    Sheet(c, "A3")
    x0, y0, x1, y1 = 392, 268, 396, 272  # mm from the top left corner
    c.rect(x0 * MM, (A3[1] - y1) * MM, (x1 - x0) * MM, (y1 - y0) * MM, stroke=0, fill=1)
    sheet_box = (x0 / A3[0], y0 / A3[1], x1 / A3[0], y1 / A3[1])
    for seed in range(200):
        boxes = {"mark": (1, sheet_box)}
        data, record = capture(c.getpdfdata(), Output("scan", "png", "poor"), seed, boxes)
        if abs(record.sheets[0].skew) > 2:
            break
    image = Image.open(io.BytesIO(data)).convert("L")
    w, h = image.size
    level = (record.ink + record.paper) / 2
    mx0, my0, mx1, my1 = boxes["mark"][1]
    # both boxes and 5 mm around them, clear of the lid that a shift bares at the edges
    left, top = round(min(mx0, sheet_box[0]) * w - 5 * w / A3[0]), round(min(my0, sheet_box[1]) * h - 5 * h / A3[1])
    right, bottom = round(max(mx1, sheet_box[2]) * w + 5 * w / A3[0]), round(max(my1, sheet_box[3]) * h + 5 * h / A3[1])
    dark = [(x, y) for y in range(top, bottom) for x in range(left, right) if image.getpixel((x, y)) < level]
    cx, cy = sum(x for x, _ in dark) / len(dark) / w, sum(y for _, y in dark) / len(dark) / h
    assert mx0 < cx < mx1 and my0 < cy < my1
    assert not (sheet_box[0] < cx < sheet_box[2] and sheet_box[1] < cy < sheet_box[3])


def test_a_document_of_several_sheets_is_captured_whole():
    pdf = draw_spec(LONG_SPEC)
    sheets = len(pdfium.PdfDocument(pdf))
    assert sheets > 1
    for container in ("pdf", "tiff"):
        data, record = capture(pdf, Output("scan", container, "fair"), 3)
        assert len(_pages(data, container)) == sheets == len(record.sheets)
    with pytest.raises(ValueError, match="one page"):
        capture(pdf, Output("scan", "jpg", "fair"), 3)


@pytest.mark.parametrize("container", ["pdf", "tiff", "jpg", "png"])
def test_a_file_keeps_its_mode_and_resolution(container):
    pdf = draw_drawing(DRAWING)
    for seed in range(40):
        data, record = capture(pdf, Output("raster", container, "fair"), seed)
        [page] = _pages(data, container)
        if record.mode == "bilevel" and container in ("tiff", "png"):
            assert page.mode == "1"
        assert abs(page.width / record.dpi * 25.4 - A3[0]) < 1
        if container == "pdf":
            pdf_page = pdfium.PdfDocument(data)[0]
            [image] = pdf_page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])
            assert image.get_metadata().bits_per_pixel == (1 if record.mode == "bilevel" else 8)
            assert abs(pdf_page.get_size()[0] / 72 * 25.4 - A3[0]) < 1
        else:
            assert round(Image.open(io.BytesIO(data)).info["dpi"][0]) == record.dpi
        if seed > 3 and record.mode == "bilevel":
            break
