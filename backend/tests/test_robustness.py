"""Некорректные входные данные не прерывают обработку комплекта."""

import pytest

from arm_web.jobs import safe_relative_path
from arm_web.pipeline.baseline.pipeline import BaselinePipeline


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


@pytest.mark.parametrize(
    "name, expected",
    [
        ("kit/Чертежи/a.pdf", "kit/Чертежи/a.pdf"),
        ("../../etc/passwd", "etc/passwd"),
        ("C:\\Users\\x\\a.pdf", "Users/x/a.pdf"),
        ("/abs/path.pdf", "abs/path.pdf"),
        ("..", "file"),
        ("bad:name?.pdf", "bad_name_.pdf"),
    ],
)
def test_safe_relative_path(name, expected):
    assert safe_relative_path(name).as_posix() == expected
