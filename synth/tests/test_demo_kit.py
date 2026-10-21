"""The demo kit generator reproduces the committed kit and its ground truth, the same way on every run."""

import io
import json
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageChops, ImageStat

from synth import demo_kit

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _assert_same_image(new: Image.Image, old: Image.Image, name: str) -> None:
    new, old = new.convert("L"), old.convert("L")
    assert new.size == old.size, name
    # Re-encoding the JPEG changes a pixel by 0.04 on average, another line of text on the sheet by 0.5.
    assert ImageStat.Stat(ImageChops.difference(new, old)).mean[0] < 0.25, name


def _assert_same_content(new: bytes, old: bytes, name: str) -> None:
    """The same text in the same places and the same pixels, up to how a platform compresses and rasterises."""
    if name.endswith(".jpg"):
        _assert_same_image(Image.open(io.BytesIO(new)), Image.open(io.BytesIO(old)), name)
        return
    new_pdf, old_pdf = pdfium.PdfDocument(new), pdfium.PdfDocument(old)
    assert len(new_pdf) == len(old_pdf), name
    for new_page, old_page in zip(new_pdf, old_pdf, strict=True):
        assert new_page.get_size() == pytest.approx(old_page.get_size()), name
        new_text, old_text = new_page.get_textpage(), old_page.get_textpage()
        assert new_text.get_text_range() == old_text.get_text_range(), name
        for i in range(new_text.count_chars()):
            assert new_text.get_charbox(i) == pytest.approx(old_text.get_charbox(i), abs=0.01), name
        images = [list(p.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])) for p in (new_page, old_page)]
        assert len(images[0]) == len(images[1]), name
        for a, b in zip(*images, strict=True):
            _assert_same_image(a.get_bitmap(render=False).to_pil(), b.get_bitmap(render=False).to_pil(), name)


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated") / "demo_kit"
    demo_kit.build(out)
    return out


def test_generator_reproduces_committed_truth(generated):
    truth = json.loads(generated.with_name("demo_kit_truth.json").read_text(encoding="utf-8"))
    assert truth == json.loads((SAMPLES / "demo_kit_truth.json").read_text(encoding="utf-8"))


def test_committed_kit_is_the_generator_output(generated):
    """On failure regenerate the kit: uv run python -m synth.demo_kit samples/demo_kit."""
    committed = _files(SAMPLES / "demo_kit")
    files = _files(generated)
    assert files.keys() == committed.keys()
    for name, data in files.items():
        if data != committed[name]:
            _assert_same_content(data, committed[name], name)


def test_same_seed_gives_the_same_bytes(generated, tmp_path):
    demo_kit.build(tmp_path / "demo_kit")
    assert _files(tmp_path / "demo_kit") == _files(generated)
