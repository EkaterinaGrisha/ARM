"""Reports against saved snapshots (plan, item «а» of 19.11)."""

import json

import snapshots
from kdcheck.run import BaselinePipeline, load_settings
from kdcheck.schemas import KitInfo, Report


def test_the_demo_kit_reads_as_its_snapshot():
    """A change of what the core gives without OCR shows here; on purpose, it records the snapshot again:
    ``uv run python eval/snapshots.py record demo --text``."""
    kit = snapshots.ROOT / "samples" / "demo_kit"
    report = BaselinePipeline(load_settings(use_ocr=False)).run(kit, kit_id="demo", kit_name="demo")
    saved = json.loads((snapshots.folder("demo") / "demo-text.json").read_text(encoding="utf-8"))
    assert snapshots.changes(saved, snapshots.snapshot(report)) == []


def test_the_time_of_processing_is_not_a_change():
    a = Report(kit=KitInfo(id="k", name="k", processed_at="2026-11-19T10:00:00", duration_sec=3.5))
    b = Report(kit=KitInfo(id="k", name="k", processed_at="2026-11-20T11:00:00", duration_sec=4.1))
    assert snapshots.changes(snapshots.snapshot(a), snapshots.snapshot(b)) == []


def test_a_changed_value_is_named_by_its_place():
    old = {"parts": [{"document_id": "d2", "title_block": {"mass": {"value": "4,51"}}}]}
    new = {"parts": [{"document_id": "d2", "title_block": {"mass": {"value": "4,5"}}}]}
    assert snapshots.changes(old, new) == ['parts[d2].title_block.mass.value: "4,51" → "4,5"']


def test_one_new_finding_does_not_shift_the_others():
    missing = {"id": "x1", "rule_id": "B1.MISSING.001", "message": "Нет чертежа А"}
    orphan = {"id": "x2", "rule_id": "B1.ORPHAN.001", "message": "Лишний чертёж Б"}
    name = {"id": "x1", "rule_id": "B1.NAME.001", "message": "Наименование В"}
    old = {"discrepancies": [missing, orphan]}
    new = {"discrepancies": [name, {**missing, "id": "x2"}, {**orphan, "id": "x3"}]}
    lines = snapshots.changes(old, new)
    assert len(lines) == 3  # the new finding, and the ids of the two that moved
    assert any(line.startswith("discrepancies[B1.NAME.001: Наименование В]: нет → ") for line in lines)


def test_documents_are_matched_by_path_not_by_order():
    a = {"path": "a.pdf", "status": "ok"}
    b = {"path": "b.pdf", "status": "damaged"}
    assert snapshots.changes({"documents": [a, b]}, {"documents": [b, a]}) == []
    assert snapshots.changes({"documents": [a]}, {"documents": [a, b]}) == [
        'documents[b.pdf]: нет → {"path": "b.pdf", "status": "damaged"}'
    ]
