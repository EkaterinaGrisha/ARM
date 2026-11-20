"""Reading the files of a kit in several processes (plan, item «г» of 19.11)."""

from collections import Counter
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest
from kdcheck.config import Settings, load_settings, settings_sha256
from kdcheck.ingest import kit
from kdcheck.ingest.document import load_document
from kdcheck.ingest.pdf import get_backend
from kdcheck.ocr import tesseract
from kdcheck.pipeline import BaselinePipeline
from reportlab.pdfgen.canvas import Canvas

DEMO = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"
OCR = load_settings(use_ocr=True)


def _files(folder: Path) -> list[Path]:
    """Four drawings and, last, a byte copy of the first."""
    paths = []
    for n in range(1, 5):
        canvas = Canvas(str(folder / f"{n}.pdf"), pagesize=(595, 842))
        canvas.drawString(72, 770, f"ARM.303411.00{n}")
        canvas.showPage()
        canvas.save()
        paths.append(folder / f"{n}.pdf")
    copy = folder / "5.pdf"
    copy.write_bytes(paths[0].read_bytes())
    return [*paths, copy]


def _processes(settings: Settings, count: int) -> Settings:
    """With OCR on: text layers need no Tesseract, and the pool reads only when OCR is on."""
    ocr = settings.models.ocr.model_copy(update={"enabled": True})
    return settings.model_copy(
        update={
            "ingest": settings.ingest.model_copy(update={"processes": count}),
            "models": settings.models.model_copy(update={"ocr": ocr}),
        }
    )


def test_processes_read_the_documents_one_process_reads(tmp_path, settings, monkeypatch):
    paths = _files(tmp_path)
    monkeypatch.setattr(tesseract, "available", lambda languages: True)
    pools = []

    class Spy(ProcessPoolExecutor):
        def __init__(self, *args, **kwargs):
            pools.append(args[0])
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(kit, "ProcessPoolExecutor", Spy)
    seen: list[int] = []
    pdfium = get_backend("pdfium")
    alone, _ = kit.load_kit(paths, tmp_path, _processes(settings, 1), pdfium, lambda n, p: None)
    docs, note = kit.load_kit(paths, tmp_path, _processes(settings, 3), pdfium, lambda n, p: seen.append(n))
    assert pools == [3] and note is None
    assert [d.info for d in docs] == [d.info for d in alone]
    assert [d.info.path for d in docs] == ["1.pdf", "2.pdf", "3.pdf", "4.pdf", "5.pdf"]
    assert [d.info.status for d in docs] == ["ok", "ok", "ok", "ok", "duplicate_file"]
    assert [ln.text for ln in docs[3].pages[0].lines] == ["ARM.303411.004"]
    assert seen == [1, 2, 3, 4, 5]


def test_without_ocr_files_are_read_in_the_job_process(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(kit, "ProcessPoolExecutor", None)  # any use of a pool fails the test
    four = settings.model_copy(update={"ingest": settings.ingest.model_copy(update={"processes": 4})})
    docs, note = kit.load_kit(_files(tmp_path), tmp_path, four, get_backend("pdfium"), lambda n, p: None)
    assert len(docs) == 5 and note is None


def test_files_a_dead_process_left_are_read_one_by_one(tmp_path, settings, monkeypatch):
    paths = _files(tmp_path)
    monkeypatch.setattr(tesseract, "available", lambda languages: True)

    class DiesAfterOne:
        """The first file is read, then the process is killed: every other file fails as a dead pool fails it."""

        def __init__(self, *args, **kwargs):
            self.submitted = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def submit(self, read, path, root, known, folder):
            future: Future = Future()
            self.submitted += 1
            if self.submitted == 1:
                future.set_result((load_document(path, root, settings, known=known), Counter()))
            else:
                future.set_exception(BrokenProcessPool("A process in the process pool was terminated abruptly"))
            return future

    monkeypatch.setattr(kit, "ProcessPoolExecutor", DiesAfterOne)
    docs, note = kit.load_kit(paths, tmp_path, _processes(settings, 2), get_backend("pdfium"), lambda n, p: None)
    assert [d.info.status for d in docs] == ["ok", "ok", "ok", "ok", "duplicate_file"]
    assert docs[4].info.problem == "Побайтовая копия файла 1.pdf: повторно не разбирается"
    assert note == "Процесс чтения файлов завершился аварийно; оставшиеся файлы (4) прочитаны по одному"


def test_the_number_of_processes_is_not_in_the_hash_of_settings(settings):
    other = settings.model_copy(update={"ingest": settings.ingest.model_copy(update={"processes": 9})})
    assert settings_sha256(other) == settings_sha256(settings)
    paged = settings.model_copy(update={"ingest": settings.ingest.model_copy(update={"page_timeout_sec": 9})})
    assert settings_sha256(paged) != settings_sha256(settings)


@pytest.mark.skipif(not tesseract.available(OCR.models.ocr.languages), reason="нужен Tesseract")
def test_the_demo_kit_reports_alike_in_one_process_and_in_four():
    reports = []
    for count in (1, 4):
        settings = OCR.model_copy(update={"ingest": OCR.ingest.model_copy(update={"processes": count})})
        report = BaselinePipeline(settings).run(DEMO, kit_id="demo", kit_name="demo")
        report.kit.processed_at, report.kit.duration_sec = "", 0.0
        reports.append(report.model_dump(mode="json"))
    assert reports[0] == reports[1]
