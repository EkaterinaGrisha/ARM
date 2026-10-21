import io
import subprocess
import sys
from pathlib import Path

import pytest
from kdcheck.config import load_settings
from kdcheck.ingest.document import load_document, sniff_media_type
from kdcheck.ingest.text import Line, Word, split_wide_lines
from kdcheck.render import RenderError, render_page
from PIL import Image
from reportlab.pdfgen.canvas import Canvas


def _pdf(path: Path, text: str | None = "ARM.303411.001", password: str | None = None, pages: int = 1) -> Path:
    canvas = Canvas(str(path), pagesize=(595, 842), encrypt=password)
    for _ in range(pages):
        if text:
            canvas.drawString(72, 770, text)
        canvas.showPage()
    canvas.save()
    return path


def test_vector_pdf_reads_text_layer(tmp_path, settings):
    doc = load_document(_pdf(tmp_path / "a.pdf", text="ABC 123"), tmp_path, settings)
    assert doc.info.status == "ok"
    assert doc.info.origin == "vector"
    assert doc.pages[0].method == "text_layer"
    assert [ln.text for ln in doc.pages[0].lines] == ["ABC 123"]
    source = doc.source(doc.pages[0], (0, 0, 10, 10))
    assert source.engine == "pdfium" and source.engine_version
    assert source.confidence == settings.ingest.confidence.text_layer


def test_encrypted_pdf_is_damaged_with_reason(tmp_path, settings):
    doc = load_document(_pdf(tmp_path / "secret.pdf", password="x"), tmp_path, settings)
    assert doc.info.status == "damaged"
    assert doc.info.problem == "Файл повреждён или не открывается: документ защищён паролем"


def test_pages_past_the_limit_are_skipped_with_a_note(tmp_path):
    path = _pdf(tmp_path / "long.pdf", text="ABC", pages=3)
    doc = load_document(path, tmp_path, load_settings(use_ocr=False, max_pages=2))
    assert doc.info.status == "ok"
    assert [p.number for p in doc.info.pages] == [1, 2]
    assert doc.info.notes == ["Прочитано страниц: 2 из 3, остальные пропущены по лимиту на файл"]
    for limit in (3, 0):  # 0: no limit
        full = load_document(path, tmp_path, load_settings(use_ocr=False, max_pages=limit))
        assert len(full.info.pages) == 3 and full.info.notes == []


def test_media_type_by_signature_not_extension():
    assert sniff_media_type(b"%PDF-1.7\n") == "application/pdf"
    assert sniff_media_type(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert sniff_media_type(b"PK\x03\x04") is None


def test_wide_gap_splits_line_into_cells():
    near = Line("a b", (0, 0, 30, 10), size=10, words=[Word("a", (0, 0, 10, 10)), Word("b", (12, 0, 30, 10))])
    far = Line("a b", (0, 0, 80, 10), size=10, words=[Word("a", (0, 0, 10, 10)), Word("b", (60, 0, 80, 10))])
    assert [ln.text for ln in split_wide_lines([near], gap=1.6)] == ["a b"]
    assert [ln.text for ln in split_wide_lines([far], gap=1.6)] == ["a", "b"]
    assert [ln.text for ln in split_wide_lines([far], gap=6)] == ["a b"]


def test_render_page_limits_size_and_checks_page_number(tmp_path):
    path = _pdf(tmp_path / "a.pdf")
    image = Image.open(io.BytesIO(render_page(path, 1, max_px=500, pdf_backend="pdfium")))
    assert max(image.size) == 500
    with pytest.raises(RenderError):
        render_page(path, 2, max_px=500, pdf_backend="pdfium")


def test_core_imports_pdf_library_only_through_the_adapter():
    code = (
        "import sys, kdcheck.ingest.document, kdcheck.render; "
        "assert 'pymupdf' not in sys.modules and 'pypdfium2' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
