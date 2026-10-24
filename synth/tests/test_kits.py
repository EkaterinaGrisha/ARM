"""Synthetic kits: the same seed gives the same kit, a clean kit is consistent, each mutation leaves its trace."""

import json
from pathlib import PurePosixPath

import pytest
from synth.kits import Kit

from synth import kits, mutations

CLEAN_SEED = 14


def _build(seed, ids) -> Kit:
    return kits.build(seed, ids, name="kit")


def _referenced(kit: Kit) -> set[str]:
    return {r.designation for spec in kit.specs.values() for r in spec.rows if r.designation}


def test_the_same_seed_gives_the_same_kit():
    ids = list(mutations.CATALOG)
    first, second = _build(12, ids), _build(12, ids)
    assert first.files == second.files
    assert first.truth() == second.truth()
    assert _build(13, ids).files != first.files


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
