"""The ground truth models read the committed truth of the demo kit and write it back byte for byte."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from synth.truth import (
    AugraphyCapture,
    AugraphySheet,
    Capture,
    DrawingTruth,
    Effect,
    ExpectedDiscrepancy,
    KitTruth,
    OtherTruth,
    SpecTruth,
)

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


def test_a_capture_says_which_degrader_made_it(tmp_path):
    """A record of synth.degrade written before Augraphy has no degrader and reads as one; a scan through Augraphy
    reads back with the effects of its sheets."""
    old = {"kind": "scan", "quality": "fair", "seed": 3, "dpi": 200, "mode": "grey", "container": "pdf"}
    augraphy = AugraphyCapture(
        quality="poor",
        seed=4,
        dpi=150,
        mode="bilevel",
        container="tiff",
        version="8.2.6",
        sheets=[
            AugraphySheet(
                pixels=(1754, 1240),
                threshold=131,
                effects=[Effect(phase="ink", name="InkBleed", settings={"intensity_range": [0.4, 0.7], "p": 0.2})],
            )
        ],
    )
    spec = {
        "kind": "specification",
        "designation": "АБВГ.301411.001",
        "name": "Привод",
        "litera": "О1",
        "revision": None,
        "rows": [],
    }
    truth = KitTruth.model_validate(
        {
            "root": "АБВГ.301411.001",
            "documents": {
                "old.pdf": {**spec, "origin": "scan", "capture": old},
                "new.tif": {**spec, "origin": "scan", "capture": augraphy.model_dump(mode="json")},
            },
        }
    )
    captures = {path: doc.capture for path, doc in truth.documents.items() if isinstance(doc, SpecTruth)}
    assert isinstance(captures["old.pdf"], Capture) and captures["old.pdf"].group == "scan:fair"
    assert captures["new.tif"] == augraphy and augraphy.group == "augraphy:poor"
    truth.write(tmp_path / "truth.json")
    assert KitTruth.read(tmp_path / "truth.json") == truth
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        SpecTruth.model_validate({**spec, "capture": {**old, "degrader": "other"}})
