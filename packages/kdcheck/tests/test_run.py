"""The baseline pipeline and the ``kdcheck run`` command on the demo kit and on broken input."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from kdcheck import pipeline
from kdcheck.cli import main
from kdcheck.config import settings_sha256
from kdcheck.extract import review
from kdcheck.pipeline import BaselinePipeline
from kdcheck.schemas import Extracted, Report
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / "samples" / "demo_kit"


def test_run_writes_three_report_forms(tmp_path, capsys):
    assert main(["run", str(KIT), "-o", str(tmp_path), "--no-ocr", "--name", "Демо"]) == 0
    report = Report.model_validate(json.loads((tmp_path / "report.json").read_text(encoding="utf-8")))
    assert report.kit.name == "Демо"
    assert report.kit.root_designation == "АРМ.303411.000"
    assert report.summary.documents_total == 23
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
    spec = (KIT / "Спецификации" / "АРМ.303411.000 Редуктор колёсный.pdf").read_bytes()
    (tmp_path / "cut.pdf").write_bytes(spec[: len(spec) // 2])  # an interrupted download
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    statuses = {d.file_name: d.status for d in report.documents}
    assert statuses == {
        "empty.pdf": "damaged",
        "broken.pdf": "damaged",
        "fake.jpg": "damaged",
        "notes.docx": "unsupported",
        "secret.pdf": "encrypted",
        "cut.pdf": "damaged",
    }
    assert all(d.problem for d in report.documents)
    assert {e.document_id for e in report.log if e.document_id} == {d.id for d in report.documents}
    assert report.tree == []


def test_service_files_of_a_system_are_no_documents_of_the_kit(tmp_path, settings):
    shutil.copy(KIT / "Чертежи" / "АРМ.303411.002 Водило.pdf", tmp_path / "Водило.pdf")
    (tmp_path / ".DS_Store").write_bytes(b"")
    (tmp_path / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0")
    (tmp_path / "__MACOSX").mkdir()
    (tmp_path / "__MACOSX" / "._Водило.pdf").write_bytes(b"\x00\x05\x16\x07")
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    assert [d.path for d in report.documents] == ["Водило.pdf"] and report.documents[0].status == "ok"
    assert any(e.message.startswith("Служебные файлы не входят в комплект (3): ") for e in report.log)


def test_a_file_named_like_a_service_one_but_not_one_stays(settings):
    names = settings.ingest.file_names
    assert names.is_ignored(Path("Чертежи/.DS_Store")) and names.is_ignored(Path("__MACOSX/a.pdf"))
    assert not names.is_ignored(Path("Чертежи/АРМ.303411.002 Водило.pdf"))
    assert not names.is_ignored(Path("Thumbs.db.pdf"))


def test_a_form_2_document_with_a_code_is_a_text_document_not_a_specification(tmp_path, settings, monkeypatch):
    spec = KIT / "Спецификации" / "АРМ.303411.000 Редуктор колёсный.pdf"
    shutil.copy(spec, tmp_path / "spec.pdf")
    (tmp_path / "note.pdf").write_bytes(spec.read_bytes() + b"%\n")  # other bytes, not a copy of the file
    parse = pipeline.specification.parse

    def note(doc, forms, eskd):  # the title block of an explanatory note on form 2, its text not a table of rows
        found = parse(doc, forms, eskd)
        if doc.info.file_name == "note.pdf":
            found.designation = Extracted(value="АРМ.303411.000 ПЗ", source=found.designation.source)
            found.name, found.rows = Extracted(value="Пояснительная записка"), []
        return found

    monkeypatch.setattr(pipeline.specification, "parse", note)
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    doc = next(d for d in report.documents if d.file_name == "note.pdf")
    assert (doc.kind, doc.designation, doc.status) == ("other", "АРМ.303411.000 ПЗ", "ok")
    assert doc.title_block and doc.title_block.designation and doc.title_block.designation.source
    assert [s.document_id for s in report.specifications] != [doc.id] and len(report.specifications) == 1
    [orphan] = [d for d in report.discrepancies if d.designation == "АРМ.303411.000 ПЗ"]
    assert (orphan.rule_id, orphan.preliminary) == ("B1.ORPHAN.001", False)


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


def test_a_lost_page_leaves_the_rest_of_the_file_in_the_report(tmp_path, settings, broken_page):
    shutil.copy(KIT / "Спецификации" / "АРМ.303411.000 Редуктор колёсный.pdf", tmp_path / "spec.pdf")
    pipeline = BaselinePipeline(settings)
    pipeline.pdf = broken_page(1)
    report = pipeline.run(tmp_path, kit_id="t", kit_name="t")
    [doc], [spec] = report.documents, report.specifications
    assert (doc.kind, doc.status) == ("specification", "warning")
    assert doc.problem == "Ошибка на странице 2 из 2: остальные страницы обработаны"
    assert [p.problem for p in doc.pages] == [None, "ошибка чтения: Failed to load page."]
    assert spec.rows and {r.source.page for r in spec.rows if r.source} == {1}
    assert [e.message for e in report.log if e.level == "error"] == ["Страница 2: ошибка чтения: Failed to load page."]


def test_a_page_that_fails_to_parse_keeps_the_rest_of_the_drawing(tmp_path, settings, monkeypatch):
    shutil.copy(KIT / "Чертежи" / "АРМ.303411.002 Водило.pdf", tmp_path / "drawing.pdf")
    pipeline = BaselinePipeline(settings)

    def broken(*args, **kwargs):
        raise ValueError("сбой разбора")

    monkeypatch.setattr(pipeline.parameters, "annotation", broken)
    report = pipeline.run(tmp_path, kit_id="t", kit_name="t")
    [doc], [part] = report.documents, report.parts
    assert doc.status == "warning" and doc.pages[0].problem == "ошибка разбора параметров: сбой разбора"
    assert part.title_block.designation and part.title_block.designation.value == "АРМ.303411.002"
    assert part.requirements and part.parameters  # the technical requirements were parsed before the page failed


@pytest.mark.parametrize(
    ("target", "stage", "empty"),
    [
        ("kdcheck.pipeline.build_tree", "дерево изделия", "tree"),
        ("kdcheck.pipeline.check", "правила Блока 1", "discrepancies"),
        ("kdcheck.pipeline.critical.select", "отбор критичных параметров", "critical_parameters"),
    ],
)
def test_a_stage_that_fails_leaves_its_part_empty_and_the_report_written(
    tmp_path, settings, monkeypatch, target, stage, empty
):
    shutil.copy(KIT / "Спецификации" / "АРМ.303411.000 Редуктор колёсный.pdf", tmp_path / "spec.pdf")
    shutil.copy(KIT / "Чертежи" / "АРМ.303411.002 Водило.pdf", tmp_path / "drawing.pdf")
    whole = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    assert whole.tree and whole.discrepancies and whole.critical_parameters

    def broken(*args, **kwargs):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(target, broken)
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    [failed] = [e for e in report.log if e.level == "error"]
    assert failed.message.startswith(f"Сбой стадии «{stage}»: ZeroDivisionError")
    assert getattr(report, empty) == []
    assert report.documents == whole.documents and report.parts and report.specifications
    kept = ["tree", "discrepancies"] if empty == "critical_parameters" else ["critical_parameters"]
    assert all(getattr(report, part) == getattr(whole, part) for part in kept)  # the rules mark the nodes they find


def test_file_names_of_a_maker_give_designations_and_names_without_sheets(tmp_path, settings):
    scan = Image.open(KIT / "Чертежи" / "АРМ.303411.203.jpg")
    for quality, name in enumerate(("АБВГ.02.115 СП Рама в сборе(1).jpg", "А1Б23-1234567СБ - Рама.jpg"), start=80):
        scan.save(tmp_path / name, quality=quality, dpi=scan.info["dpi"])  # other bytes: no byte copy of each other
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")  # OCR off: scans keep file names
    assert {d.designation: d.name for d in report.documents} == {
        "АБВГ.02.115": "Рама в сборе",
        "А1Б23-1234567 СБ": "Рама",
    }
    assert not [e for e in report.log if "ни одна грамматика" in e.message]  # no look left unknown
    assert any("по грамматикам classifier, decimal, model:" in e.message for e in report.log)


def test_a_kit_of_unknown_designations_says_which_look_to_add(tmp_path, settings):
    scan = KIT / "Чертежи" / "АРМ.303411.203.jpg"
    for name in ("ЖЖ-12-345 Рама.jpg", "ЖЖ-12-346 Ось.jpg"):
        shutil.copy(scan, tmp_path / name)
    report = BaselinePipeline(settings).run(tmp_path, kit_id="t", kit_name="t")
    assert [e.level for e in report.log if "вида АА-99-999" in e.message] == ["warning"]


def _one_sheet_per_file(pdf: Path) -> list[Path]:
    """Each page of ``pdf`` a file of its own, «<stem> (N).pdf», as a kit keeps a document one sheet per file."""
    source = pdfium.PdfDocument(pdf)
    paths = []
    for i in range(len(source)):
        sheet = pdfium.PdfDocument.new()
        sheet.import_pages(source, [i])
        paths.append(pdf.with_name(f"{pdf.stem} ({i + 1}).pdf"))
        sheet.save(paths[-1])
    source.close()
    pdf.unlink()
    return paths


def _rows(report: Report, designation: str) -> list[tuple[str | None, str | None]]:
    return [
        (row.section, str(row.designation.value) if row.designation else None)
        for spec in report.specifications
        if spec.designation is not None and spec.designation.value == designation
        for row in spec.rows
    ]


def test_a_specification_kept_one_sheet_per_file_is_one_document(tmp_path, settings):
    root = "АРМ.303411.000"
    whole = BaselinePipeline(settings).run(KIT, kit_id="t", kit_name="t")
    kit = tmp_path / "kit"
    shutil.copytree(KIT, kit)
    first, second = _one_sheet_per_file(kit / "Спецификации" / f"{root} Редуктор колёсный.pdf")
    split = BaselinePipeline(settings).run(kit, kit_id="t", kit_name="t")
    assert _rows(split, root) == _rows(whole, root) and len(_rows(whole, root)) > 0
    files = {d.path.rsplit("/", 1)[-1]: d for d in split.documents}
    one, two = files[first.name], files[second.name]
    assert (one.sheet, one.sheet_of, two.sheet, two.sheet_of) == (1, None, 2, one.id)
    found = {(x.rule_id, x.designation) for x in split.discrepancies}
    assert found == {(x.rule_id, x.designation) for x in whole.discrepancies}

    second.unlink()  # the title block of the first sheet still counts two
    lost = BaselinePipeline(settings).run(kit, kit_id="t", kit_name="t")
    assert ("B1.SHEETS.001", root) in {(x.rule_id, x.designation) for x in lost.discrepancies}
