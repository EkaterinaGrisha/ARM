"""The ground truth models read the committed truth of the demo kit and write it back byte for byte."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from synth.truth import DrawingTruth, ExpectedDiscrepancy, KitTruth, OtherTruth, SpecTruth

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"


def test_committed_truth_reads_back_as_written(tmp_path):
    truth = KitTruth.read(SAMPLES / "demo_kit_truth.json")
    truth.write(tmp_path / "truth.json")
    assert (tmp_path / "truth.json").read_bytes() == (SAMPLES / "demo_kit_truth.json").read_bytes()
    kinds = {type(t) for t in truth.documents.values()}
    assert kinds == {SpecTruth, DrawingTruth}


def test_a_typo_in_a_key_does_not_pass(tmp_path):
    text = (SAMPLES / "demo_kit_truth.json").read_text(encoding="utf-8").replace('"litera"', '"litre"', 1)
    (tmp_path / "truth.json").write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError, match="litre"):
        KitTruth.read(tmp_path / "truth.json")


def test_a_kit_labelled_by_hand_reads_back(tmp_path):
    """A real kit: a scheme beside the drawings, a row without a designation, the reason and a disputed case."""
    truth = KitTruth(
        root="",
        documents={"schematic.pdf": OtherTruth(designation="", name="Схема электрическая принципиальная")},
        discrepancies=[
            ExpectedDiscrepancy(
                type="position_anomaly", designation="", name="Корпус-крышка", comment="в строке нет обозначения"
            )
        ],
        notes=["позиции без обозначений сопоставляем по наименованию"],
    )
    truth.write(tmp_path / "gold.json")
    assert KitTruth.read(tmp_path / "gold.json") == truth


def test_gold_files_read_as_kit_truth():
    """The Block 1 gold set and its drafts (eval/gold) keep the format the evaluation reads."""
    for path in sorted((ROOT / "eval" / "gold").rglob("*.json")):
        KitTruth.read(path)
