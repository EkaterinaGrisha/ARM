"""Baseline pipeline on the committed demo kit with a known ground truth."""

from arm_web.evaluate import evaluate
from arm_web.models import Report


def test_finds_all_planted_discrepancies(demo_kit, demo_report):
    _, truth = demo_kit
    expected = {(d["type"], d["designation"]) for d in truth["discrepancies"]}
    found = {(d.type, d.designation) for d in demo_report.discrepancies}
    assert found == expected


def test_problem_files_are_categorized(demo_kit, demo_report):
    _, truth = demo_kit
    statuses = {d.path: d.status for d in demo_report.documents}
    for path, status in truth["problem_files"].items():
        assert statuses[path] == status, path
    assert demo_report.summary.documents_total == len(truth["documents"]) + len(truth["problem_files"])


def test_tree_root_and_specifications(demo_report):
    assert demo_report.kit.root_designation == "АРМ.303411.000"
    assert len(demo_report.specifications) == 3
    rows = {r.designation.value for s in demo_report.specifications for r in s.rows if r.designation}
    assert "АРМ.303411.004" in rows and "АРМ.303411.000 СБ" in rows


def test_vector_documents_are_extracted_exactly(demo_kit, demo_report):
    _, truth = demo_kit
    # сканы в тестах обрабатываются без OCR — оцениваем только векторные документы
    vector_only = {**truth, "documents": {k: v for k, v in truth["documents"].items() if v.get("origin", "vector") == "vector"}}
    quality = evaluate(demo_report, vector_only)
    title = next(m for m in quality.metrics if m.name == "Основная надпись — векторные PDF")
    assert title.accuracy == 1.0
    params = next(m for m in quality.metrics if m.name == "Параметры технических требований")
    assert params.precision == 1.0 and params.recall == 1.0
    rows = next(m for m in quality.metrics if m.name.startswith("Строки спецификаций"))
    assert rows.accuracy == 1.0


def test_scans_without_ocr_use_file_name(demo_report):
    scan = next(d for d in demo_report.documents if d.path.endswith("АРМ.303411.203.jpg"))
    assert scan.status == "warning" and scan.designation == "АРМ.303411.203"
    part = next(p for p in demo_report.parts if p.document_id == scan.id)
    assert part.title_block.designation.source.method == "filename"


def test_every_value_is_traceable(demo_report):
    docs = {d.id for d in demo_report.documents}
    for part in demo_report.parts:
        for p in part.parameters:
            assert p.source and p.source.document_id in docs and p.source.bbox
            x0, y0, x1, y1 = p.source.bbox
            assert 0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1


def test_critical_parameters_have_reasons(demo_report):
    assert demo_report.critical_parameters
    rules = {c.rule for c in demo_report.critical_parameters}
    assert {"tight_fit", "fine_roughness", "high_hardness"} <= rules
    assert all(c.reason and 0 <= c.score <= 1 for c in demo_report.critical_parameters)


def test_report_roundtrips_through_json(demo_report):
    again = Report.model_validate_json(demo_report.model_dump_json(exclude_none=True))
    assert again.summary == demo_report.summary
