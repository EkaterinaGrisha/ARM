"""Synthetic kits: the same seed gives the same kit, a clean kit is consistent, each mutation leaves its trace."""

import json
import random
import re
from pathlib import PurePosixPath

import pytest
from synth.kits import Kit
from synth.truth import DrawingTruth

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


def _covered(kit: Kit) -> tuple[set[str], set[str], set[str]]:
    """What the drawings, the specifications and the assembly drawings of a kit cover, group documents with all
    their variants."""
    drawings = {d.designation for d in kit.drawings} | {v.designation for d in kit.drawings for v in d.variants}
    specs = set(kit.specs) | {v for spec in kit.specs.values() for v in spec.variants}
    assemblies = {d.designation.removesuffix(" СБ") for d in kit.drawings if d.assembly}
    assemblies |= {v.designation for d in kit.drawings if d.assembly for v in d.variants}
    return drawings, specs, assemblies


def test_a_clean_kit_has_a_document_for_every_position_and_expects_nothing():
    kit = _build(CLEAN_SEED, [])
    drawings, specs, assemblies = _covered(kit)
    assert any(spec.group for spec in kit.specs.values())
    for spec in kit.specs.values():
        for row in spec.rows:
            if row.section == "Документация":
                assert row.designation in drawings
            elif row.section == "Сборочные единицы":
                assert row.designation in specs and row.designation in assemblies
            elif row.section == "Детали" and "БЧ" not in (row.fmt, row.note):
                assert row.designation in drawings
    assert {d.designation for d in kit.drawings} - {f"{u} СБ" for u in kit.specs} <= _referenced(kit)
    truth = kit.truth()
    assert truth.discrepancies == [] and truth.problem_files == {} and truth.mutations == []
    assert set(truth.documents) == set(kit.files)


def _group_kits(count: int = 30) -> list[Kit]:
    return [_build(seed, []) for seed in range(1, count + 1)]


def test_a_group_specification_by_variant_a_holds_the_variable_data_of_each_variant_in_the_same_positions():
    """GOST 2.113, 5.5.1: the constant data first, then each variant in turn; the variable items of the base variant
    go on with the positions, and the other variants hold theirs in the same positions."""
    specs = [s for kit in _group_kits() for s in kit.specs.values() if s.group == "A"]
    assert len(specs) >= 5
    for spec in specs:
        constant = [r for r in spec.rows if not r.variant]
        assert spec.rows[: len(constant)] == constant and all(r.qty and not r.quantities for r in spec.rows if r.pos)
        assert [r.variant for r in spec.rows[len(constant) :]] == sorted(
            (r.variant for r in spec.rows[len(constant) :]), key=spec.variants.index
        )
        last = max((int(r.pos) for r in constant if r.pos), default=0)  # all items may vary
        positions = {v: [r.pos for r in spec.rows if r.variant == v] for v in spec.variants}
        assert all(positions[v] == positions[spec.variants[0]] for v in spec.variants)
        assert sorted(int(p) for p in positions[spec.variants[0]]) == list(
            range(last + 1, last + 1 + len(positions[spec.variants[0]]))
        )
        assert all(not r.written for r in spec.rows if r.variant)  # each variant under a heading of its own
        for variant in spec.variants[1:]:  # what a later variant holds instead is a variant of the base one's item
            for base, other in zip(
                *(([r for r in spec.rows if r.variant == v]) for v in (spec.variants[0], variant)), strict=True
            ):
                assert other.designation == base.designation or other.designation.startswith(f"{base.designation}-")


def test_a_group_specification_by_variant_b_gives_each_variant_a_column_of_quantities():
    """GOST 2.113, 5.5.2 and form 5: a quantity in the column of each variant that holds the row, «×» for the
    documents; the variants of a part share its position, and below the full designation write only «-01» (5.3)."""
    specs = [s for kit in _group_kits() for s in kit.specs.values() if s.group == "B"]
    assert len(specs) >= 5
    for spec in specs:
        assert 2 <= len(spec.variants) <= 3 and all(not r.qty and not r.variant for r in spec.rows)
        for row in spec.rows:
            assert row.quantities and set(row.quantities) <= set(spec.variants)
            if row.section == "Документация":
                assert row.quantities == dict.fromkeys(spec.variants, "×")
        shortened = [i for i, r in enumerate(spec.rows) if r.written]
        assert shortened
        for i in shortened:
            row, above = spec.rows[i], spec.rows[i - 1]
            assert row.designation.endswith(row.written) and re.fullmatch(r"-\d{2}", row.written)
            assert above.designation.split("-")[0] == row.designation.split("-")[0] and not row.fmt
        alternatives = [r for r in spec.rows if r.written and not r.pos]
        assert alternatives and all(len(r.quantities) == 1 for r in alternatives)


def test_group_documents_cover_every_variant_the_specifications_name():
    """A group drawing lists the variants of its part in the table of variants and «См. табл.» for the mass; a group
    assembly drawing lists the variants of its specification; the parent holds some of the variants."""
    for kit in _group_kits(10):
        truth = kit.truth()
        for spec in kit.specs.values():
            if not spec.variants:
                continue
            drawing = kit.drawing(f"{spec.designation} СБ")
            assert drawing and [v.designation for v in drawing.variants] == spec.variants
            parent = next(
                s for s in kit.specs.values() if spec.designation in {r.designation.split("-")[0] for r in s.rows}
            )
            held = [r.designation for r in parent.rows if r.designation.split("-")[0] == spec.designation]
            assert held and set(held) <= set(spec.variants)
        for path, doc in truth.documents.items():
            if isinstance(doc, DrawingTruth) and doc.variants:
                assert doc.mass == "См. табл." and doc.variants[0].designation == doc.designation.removesuffix(" СБ")
                assert [v.written for v in doc.variants[1:]] == [
                    v.designation.removeprefix(doc.variants[0].designation) for v in doc.variants[1:]
                ]
                assert kit.files[path].startswith(b"%PDF") and doc.boxes


def test_a_kit_takes_its_lettering_by_its_seed_and_the_rest_does_not_depend_on_it():
    fonts = [kits.build(seed, [], scan_share=0, raster_share=0).font_type for seed in range(1, 41)]
    assert 12 <= fonts.count("B") <= 28
    a = kits.build(5, [], scan_share=0, raster_share=0, type_b_share=0)
    b = kits.build(5, [], scan_share=0, raster_share=0, type_b_share=1)
    assert (a.font_type, b.font_type) == ("A", "B")

    def content(kit: Kit) -> list[tuple[str, ...]]:  # the documents with names as one line: type B wraps sooner
        rows = [(s.file, r.designation, r.name.replace("\n", " "), r.qty) for s in kit.specs.values() for r in s.rows]
        return rows + [(d.file, d.designation, " ".join(d.requirements).replace("\n", " ")) for d in kit.drawings]

    assert content(a) == content(b)


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
