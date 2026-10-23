"""The ground truth models read the committed truth of the demo kit and write it back byte for byte."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from synth.truth import DrawingTruth, KitTruth, SpecTruth

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


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
