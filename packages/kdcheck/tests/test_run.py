"""The baseline pipeline and the ``kdcheck run`` command on the demo kit and on broken input."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kdcheck.cli import main
from kdcheck.config import settings_sha256
from kdcheck.extract import review
from kdcheck.pipeline import BaselinePipeline
from kdcheck.schemas import Report
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / "samples" / "demo_kit"


def test_run_writes_three_report_forms(tmp_path, capsys):
    assert main(["run", str(KIT), "-o", str(tmp_path), "--no-ocr", "--name", "Демо"]) == 0
    report = Report.model_validate(json.loads((tmp_path / "report.json").read_text(encoding="utf-8")))
    assert report.kit.name == "Демо"
    assert report.kit.root_designation == "АРМ.303411.000"
    assert report.summary.documents_total == 22
    assert report.discrepancies and all(d.rule_id for d in report.discrepancies)
    assert (tmp_path / "report.xlsx").stat().st_size > 0
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "АРМ.303411.000" in html and report.discrepancies[0].rule_id in html
    assert "Отчёт:" in capsys.readouterr().out


def test_run_reports_progress_to_a_file_for_the_api(tmp_path):
    progress = tmp_path / "progress.json"
    args = ["run", str(KIT), "-o", str(tmp_path), "--no-ocr", "--kit-id", "job42", "--progress-file", str(progress)]
    assert main(args) == 0
    assert json.loads(progress.read_text(encoding="utf-8")) == {"stage": "Готово", "progress": 1.0, "message": None}
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["kit"]["id"] == "job42"


def test_run_rejects_a_missing_folder(tmp_path):
    assert main(["run", str(tmp_path / "nope"), "-o", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


def test_ocr_can_be_turned_off():
    code = "from kdcheck.run import ocr_available; assert ocr_available() is False"
    subprocess.run([sys.executable, "-c", code], env={**os.environ, "ARM_OCR": "0"}, check=True)


def test_module_entry_point():
    result = subprocess.run([sys.executable, "-m", "kdcheck", "run", "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--no-ocr" in result.stdout


def test_bad_files_get_statuses(tmp_path, settings):
    (tmp_path / "empty.pdf").write_bytes(b"")
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    (tmp_path / "fake.jpg").write_bytes(b"\xff\xd8\xff" + b"not a jpeg" * 10)
    (tmp_path / "notes.docx").write_bytes(b"PK\x03\x04 zip")
    canvas = Canvas(str(tmp_path / "secret.pdf"), encrypt="x")
    canvas.drawString(72, 770, "АРМ.303411.001")
    canvas.save()
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    statuses = {d.file_name: d.status for d in report.documents}
    assert statuses == {
        "empty.pdf": "damaged",
        "broken.pdf": "damaged",
        "fake.jpg": "damaged",
        "notes.docx": "unsupported",
        "secret.pdf": "encrypted",
    }
    assert all(d.problem for d in report.documents)
    assert {e.document_id for e in report.log if e.document_id} == {d.id for d in report.documents}
    assert report.tree == []


def test_byte_copy_is_read_once_and_is_no_duplicate_designation(tmp_path, settings):
    drawing = KIT / "Чертежи" / "АРМ.303411.002 Водило.pdf"
    (tmp_path / "архив").mkdir()
    for target in (tmp_path / "Водило.pdf", tmp_path / "архив" / "Водило.pdf"):
        shutil.copy(drawing, target)
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    statuses = {d.path: (d.status, d.problem) for d in report.documents}
    assert statuses["Водило.pdf"][0] == "ok"
    assert statuses["архив/Водило.pdf"] == (
        "duplicate_file",
        "Побайтовая копия файла Водило.pdf: повторно не разбирается",
    )
    assert [p.designation for p in report.parts] == ["АРМ.303411.002"]
    assert report.discrepancies == []
    assert report.summary.documents_by_status == {"ok": 1, "duplicate_file": 1}


def test_empty_kit(tmp_path, settings):
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    assert report.summary.documents_total == 0


def test_report_records_the_settings_hash_and_engines(tmp_path, settings):
    assert main(["run", str(KIT), "-o", str(tmp_path), "--no-ocr"]) == 0
    report = Report.model_validate_json((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report.kit.config_sha256 == settings_sha256(settings)
    engines = {e.name: e.version for e in report.kit.engines}
    assert {"pdfium", "pypdfium2", "pillow"} <= set(engines) and all(engines.values())
    assert "tesseract" not in engines  # OCR was off
    vector = [p.title_block.designation.source for p in report.parts if p.title_block.designation]
    assert vector and all(s.engine for s in vector if s.method == "text_layer")
    assert {s.engine_version for s in vector if s.engine == "pdfium"} == {engines["pdfium"]}


def test_run_stops_on_broken_settings(tmp_path, capsys):
    config = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", config)
    criticality = config / "criticality.yaml"
    criticality.write_text(criticality.read_text(encoding="utf-8").replace("score: 0.75", "score: 7.5", 1), "utf-8")
    assert main(["run", str(KIT), "-o", str(tmp_path / "out"), "--config", str(config)]) == 2
    assert "criticality.yaml: tight_fit.score" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_values_to_check_are_marked(settings):
    report = BaselinePipeline(settings).run(KIT, kit_id="t", kit_name="t")  # OCR off: scans keep file names
    found = list(review.values(report))
    assert found and all(ex.status for ex in found)
    by_method: dict[str | None, set[str | None]] = {}
    for ex in found:
        by_method.setdefault(ex.source.method if ex.source else None, set()).add(ex.status)
    assert by_method == {"text_layer": {"ok"}, "filename": {"low_confidence"}}
