"""The demo kit generator reproduces the committed ground truth."""

import json
from pathlib import Path

import pytest

from arm_web import demo_kit

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def test_generator_reproduces_committed_truth(tmp_path):
    try:
        demo_kit._font_buffer()
    except RuntimeError as exc:
        pytest.skip(f"no Cyrillic font for the generator: {exc}")
    truth_path = tmp_path / "truth.json"
    demo_kit.build(tmp_path / "demo_kit", truth_path)
    generated = json.loads(truth_path.read_text(encoding="utf-8"))
    committed = json.loads((SAMPLES / "demo_kit_truth.json").read_text(encoding="utf-8"))
    assert generated == committed
    generated_files = sorted(
        p.relative_to(tmp_path / "demo_kit").as_posix() for p in (tmp_path / "demo_kit").rglob("*") if p.is_file()
    )
    committed_files = sorted(
        p.relative_to(SAMPLES / "demo_kit").as_posix() for p in (SAMPLES / "demo_kit").rglob("*") if p.is_file()
    )
    assert generated_files == committed_files
