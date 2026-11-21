"""A customer's kit against its gold: rows pair without boxes, Block 1 counts by type and designation."""

from types import SimpleNamespace

import arm_gold
from kdcheck.schemas import Extracted, SpecRow
from synth.truth import ExpectedDiscrepancy, KitTruth, RowTruth


def row(i: int, designation: str | None, name: str) -> SpecRow:
    return SpecRow(
        id=f"r{i}",
        designation=Extracted(value=designation) if designation else None,
        name=Extracted(value=name),
    )


def test_rows_pair_by_designation_then_by_name_and_a_variant_by_the_designation_as_written():
    want = [
        RowTruth(section="Детали", designation="ШСМГ.01.073-10", written="-10", name="Муфта переходная"),
        RowTruth(section="Стандартные изделия", name="Болт М12-6gx40 ГОСТ 7798-70"),
        RowTruth(section="Детали", designation="ШСМГ.01.074", name="Контргайка"),
        RowTruth(section="Детали", designation="ШСМГ.01.075", name="Шайба"),
    ]
    got = [row(1, None, "Болт М12-6gx40 ГОСТ 7798-70"), row(2, "ШСМГ.01.074", "Контрган"), row(3, "-10", "Муфта")]
    assert [r.id if r else None for r in arm_gold.pair_rows(want, got)] == ["r3", "r1", "r2", None]


def test_block1_counts_found_missed_and_extra_by_type_and_a_second_finding_of_one_designation_is_extra():
    truth = KitTruth(
        root="",
        documents={},
        discrepancies=[
            ExpectedDiscrepancy(type="name_mismatch", designation="ШСМГ.01.070"),
            ExpectedDiscrepancy(type="missing_drawing", designation="ШСМГ.01.037"),
        ],
    )

    def finding(kind: str, designation: str, rule: str) -> SimpleNamespace:
        return SimpleNamespace(type=kind, designation=designation, rule_id=rule, preliminary=False, message="")

    report = SimpleNamespace(
        discrepancies=[
            finding("name_mismatch", "ШСМГ.01.070", "B1.NAME.001"),
            finding("name_mismatch", "ШСМГ.01.070", "B1.NAME.002"),
            finding("missing_drawing", "ШСМГ.01.034-10", "B1.MISSING.001"),
        ]
    )
    eskd = SimpleNamespace(normalize_designation=lambda text: text.strip() if text else None)
    result, lines = arm_gold.block1(report, truth, eskd)
    assert result["by_type"] == {
        "missing_drawing": {"expected": 1, "extra": 1},
        "name_mismatch": {"expected": 1, "found": 1, "extra": 1},
    }
    assert [line.split(" | ")[0] for line in lines] == ["| лишнее", "| верно", "| лишнее", "| пропущено"]
