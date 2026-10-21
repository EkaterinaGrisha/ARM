"""The demo kit generator reproduces the committed ground truth, the same way on every run."""

import json
from pathlib import Path

import pytest

from synth import demo_kit

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated") / "demo_kit"
    demo_kit.build(out)
    return out


def test_generator_reproduces_committed_truth(generated):
    truth = json.loads(generated.with_name("demo_kit_truth.json").read_text(encoding="utf-8"))
    assert truth == json.loads((SAMPLES / "demo_kit_truth.json").read_text(encoding="utf-8"))
    assert _files(generated).keys() == _files(SAMPLES / "demo_kit").keys()


def test_same_seed_gives_the_same_bytes(generated, tmp_path):
    demo_kit.build(tmp_path / "demo_kit")
    assert _files(tmp_path / "demo_kit") == _files(generated)
