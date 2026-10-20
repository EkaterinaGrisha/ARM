"""The baseline pipeline and the ``kdcheck run`` command on the demo kit and on broken input."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from kdcheck.cli import main
from kdcheck.pipeline import BaselinePipeline
from kdcheck.schemas import Report

pytest.importorskip("pymupdf")

KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"


def test_run_writes_three_report_forms(tmp_path, capsys):
    assert main(["run", str(KIT), "-o", str(tmp_path), "--no-ocr", "--name", "Демо"]) == 0
    report = Report.model_validate(json.loads((tmp_path / "report.json").read_text(encoding="utf-8")))
    assert report.kit.name == "Демо"
    assert report.kit.root_designation == "АРМ.303411.000"
    assert report.summary.documents_total == 22
    assert (tmp_path / "report.xlsx").stat().st_size > 0
    assert "АРМ.303411.000" in (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Отчёт:" in capsys.readouterr().out


def test_run_rejects_a_missing_folder(tmp_path):
    assert main(["run", str(tmp_path / "nope"), "-o", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


def test_ocr_can_be_turned_off():
    code = "from kdcheck.run import ocr_available; assert ocr_available() is False"
    subprocess.run([sys.executable, "-c", code], env={**os.environ, "ARM_OCR": "0"}, check=True)


def test_module_entry_point():
    result = subprocess.run([sys.executable, "-m", "kdcheck", "run", "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--no-ocr" in result.stdout


def test_bad_files_get_statuses(tmp_path):
    (tmp_path / "empty.pdf").write_bytes(b"")
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    (tmp_path / "fake.jpg").write_bytes(b"\xff\xd8\xff" + b"not a jpeg" * 10)
    (tmp_path / "notes.docx").write_bytes(b"PK\x03\x04 zip")
    report = BaselinePipeline(use_ocr=False).run(tmp_path, kit_id="t", kit_name="t")
    statuses = {d.file_name: d.status for d in report.documents}
    assert statuses == {
        "empty.pdf": "damaged",
        "broken.pdf": "damaged",
        "fake.jpg": "damaged",
        "notes.docx": "unsupported",
    }
    assert all(d.problem for d in report.documents)
    assert report.tree == []


def test_empty_kit(tmp_path):
    report = BaselinePipeline(use_ocr=False).run(tmp_path, kit_id="t", kit_name="t")
    assert report.summary.documents_total == 0
