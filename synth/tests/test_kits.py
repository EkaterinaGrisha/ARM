"""Synthetic kits: the same seed gives the same kit, a clean kit is consistent, each mutation leaves its trace."""

import json
import random
from pathlib import PurePosixPath

import pytest
from synth.kits import Kit

from synth import degrade, kits, mutations

CLEAN_SEED = 14


def _build(seed, ids, *, captured: bool = False) -> Kit:
    """A kit of vector documents: scanning costs seconds a kit and is tested on its own, unless ``captured``."""
    if captured:
        return kits.build(seed, ids, name="kit")
    return kits.build(seed, ids, name="kit", scan_share=0, raster_share=0)


def _referenced(kit: Kit) -> set[str]:
    return {r.designation for spec in kit.specs.values() for r in spec.rows if r.designation}


def test_the_same_seed_gives_the_same_kit():
    ids = list(mutations.CATALOG)
    first, second = _build(12, ids, captured=True), _build(12, ids, captured=True)
    assert first.captures and first.files == second.files
    assert first.truth() == second.truth()
    assert _build(13, ids, captured=True).files != first.files


def test_a_clean_kit_has_a_document_for_every_position_and_expects_nothing():
    kit = _build(CLEAN_SEED, [])
    drawings = {d.designation for d in kit.drawings}
    for spec in kit.specs.values():
        for row in spec.rows:
            if row.section == "Документация":
                assert row.designation in drawings
            elif row.section == "Сборочные единицы":
                assert row.designation in kit.specs and f"{row.designation} СБ" in drawings
            elif row.section == "Детали" and "БЧ" not in (row.fmt, row.note):
                assert row.designation in drawings
    assert {d.designation for d in kit.drawings} - {f"{u} СБ" for u in kit.specs} <= _referenced(kit)
    truth = kit.truth()
    assert truth.discrepancies == [] and truth.problem_files == {} and truth.mutations == []
    assert set(truth.documents) == set(kit.files)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_trees_reach_their_depth_and_designations_are_unique(seed):
    kit = _build(seed, [])
    units = list(kit.root.walk())
    designations = [u.designation for u in units] + [p.designation for u in units for p in u.parts]
    assert len(designations) == len(set(designations))
    assert 2 <= max(u.level for u in units) + 1 <= 4


def _first_kit_with(mutation_id) -> Kit:
    for seed in range(1, 40):
        kit = _build(seed, [mutation_id])
        if kit.mutations == [mutation_id]:
            return kit
    raise AssertionError(f"no kit takes {mutation_id}")


@pytest.mark.parametrize("mutation_id", list(mutations.CATALOG))
def test_each_mutation_writes_what_it_plants(mutation_id):
    kit = _first_kit_with(mutation_id)
    clean = _build(kit.seed, [])
    truth = kit.truth()
    assert all(d.mutation == mutation_id for d in truth.discrepancies)
    for path, status in truth.problem_files.items():
        assert path in kit.files and path not in truth.documents, (path, status)
    added = set(kit.files) - set(clean.files)
    removed = set(clean.files) - set(kit.files)
    expected = {(d.type, d.rule_id) for d in truth.discrepancies}
    match mutation_id:
        case "drop_part_drawing" | "drop_assembly_drawing":
            assert len(removed) == 1 and not added
        case "drop_assembly_spec":
            assert len(removed) == 1 and ("missing_specification", "B1.SPEC.001") in expected
            orphans = [d.designation for d in truth.discrepancies if d.rule_id == "B1.ORPHAN.001"]
            assert orphans and not set(orphans) & _referenced(kit)
        case "extra_drawing":
            [path] = added
            [orphan] = truth.discrepancies
            assert (
                orphan.designation not in _referenced(kit) and truth.documents[path].designation == orphan.designation
            )
        case "duplicate_designation" | "second_version":
            [path] = added
            twin = truth.documents[path]
            assert sum(d.designation == twin.designation for d in truth.documents.values()) == 2
        case "byte_copy":
            [path] = added
            assert truth.problem_files == {path: "duplicate_file"}
            original = next(p for p in kit.files if p != path and kit.files[p] == kit.files[path])
            assert PurePosixPath(original) < PurePosixPath(path)  # the check keeps the first in path order
        case "foreign_document" | "unsupported_file":
            assert len(added) == 1 and not truth.discrepancies
        case "damage_file" | "encrypt_file" | "unreadable_scan":
            [path] = truth.problem_files
            assert expected == {("missing_drawing", "B1.MISSING.001")}
        case _:
            assert not added and not removed
    if mutation_id not in {"foreign_document", "unsupported_file", "byte_copy"}:
        assert truth.discrepancies


def test_the_corpus_writes_kits_truths_and_a_summary(tmp_path):
    truths = kits.corpus(tmp_path, 3, seed=5, clean_every=2)
    assert [t.mutations == [] for t in truths] == [False, True, False]
    summary = json.loads((tmp_path / "corpus.json").read_text(encoding="utf-8"))
    assert [k["kit"] for k in summary["kits"]] == ["kit_001", "kit_002", "kit_003"]
    for index, truth in enumerate(truths, start=1):
        folder = tmp_path / f"kit_{index:03d}"
        files = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
        assert set(truth.documents) <= files and set(truth.problem_files) <= files


def test_rasters_and_scans_of_a_kit_carry_their_capture_in_the_truth():
    """Specifications as well as drawings; each file is what its capture says, of all its sheets, with its boxes."""
    for seed in range(1, 40):
        kit = kits.build(seed, [], scan_share=0.5, raster_share=0.3)
        truth = kit.truth()
        captured = {path: doc for path, doc in truth.documents.items() if doc.origin != "vector"}
        if any(doc.kind == "specification" for doc in captured.values()):
            break
    assert {doc.kind for doc in captured.values()} >= {"specification", "part_drawing"}
    assert {doc.capture.kind for doc in captured.values() if doc.capture} == {"raster", "scan"}
    assert {doc.capture.quality for doc in captured.values() if doc.capture} == {"good", "fair", "poor"}
    for path, doc in truth.documents.items():
        if doc.origin == "vector":
            assert doc.capture is None and kit.files[path].startswith(b"%PDF")
            continue
        assert doc.capture is not None and doc.capture.kind == doc.origin
        assert PurePosixPath(path).suffix == kits.SUFFIXES[doc.capture.container]
        assert len(doc.capture.sheets) == (doc.sheets if doc.origin == "scan" else 0)
        assert doc.boxes and all(0 <= b.bbox[0] < b.bbox[2] <= 1 for b in doc.boxes.values())
    summary = {f"{d.capture.kind}:{d.capture.quality}" for d in captured.values() if d.capture}
    assert summary == {f"{c.kind}:{c.quality}" for c in kit.captures.values()}


def test_the_degrader_changes_the_captured_files_only():
    """The stress corpus scans the kits of the main corpus: the documents and the seed of each capture do not depend
    on what makes the pixels, so each scan pairs with the scan of the same document by synth.degrade."""
    seeds: list[int] = []

    def blank(pdf, output, seed, boxes):  # a degrader that keeps no pixels at all
        seeds.append(seed)
        return b"blank", degrade.plan(output, [(210.0, 297.0)], seed, random.Random(seed))

    plain = kits.build(21, [], scan_share=0.5, raster_share=0.3)
    other = kits.build(21, [], scan_share=0.5, raster_share=0.3, capture=blank)
    captured = set(plain.captures)
    assert captured and set(other.captures) == captured and all(other.files[p] == b"blank" for p in captured)
    assert {p: f for p, f in plain.files.items() if p not in captured} == {
        p: f for p, f in other.files.items() if p not in captured
    }
    assert seeds == [c.seed for c in plain.captures.values()]
    assert plain.truth().documents.keys() == other.truth().documents.keys()
