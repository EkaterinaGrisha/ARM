"""The stress corpus: its manifest finds every file that differs, and the committed manifest describes a whole corpus.

None of this needs Augraphy: the corpus is checked by sha256 wherever the repository runs.
"""

from pathlib import Path

import pytest
from synth.stress import MANIFEST, Manifest, Parameters, digests, source_digest, verify
from synth.truth import AugraphyCapture, AugraphySheet, Effect

ROOT = Path(__file__).resolve().parents[2]


def _manifest(out: Path) -> Manifest:
    scan = AugraphyCapture(
        quality="fair",
        seed=7,
        dpi=200,
        mode="grey",
        container="png",
        version="8.2.6",
        sheets=[AugraphySheet(pixels=(10, 20), effects=[Effect(phase="post", name="Jpeg", settings={"p": 0.2})])],
    )
    return Manifest(
        built="2026-10-28",
        parameters=Parameters(),
        environment={},
        versions={},
        source=source_digest(),
        files=digests(out),
        scans={"kit_001/Чертежи/лист.png": scan},
    )


def test_verify_finds_missing_changed_and_unlisted_files(tmp_path):
    (tmp_path / "kit_001" / "Чертежи").mkdir(parents=True)
    (tmp_path / "kit_001" / "Чертежи" / "лист.png").write_bytes(b"png")
    (tmp_path / "kit_001_truth.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"notes of the OS")  # hidden files are no part of the corpus
    manifest = _manifest(tmp_path)
    assert set(manifest.files) == {"kit_001/Чертежи/лист.png", "kit_001_truth.json"}
    assert verify(tmp_path, manifest) == []
    (tmp_path / "kit_001" / "Чертежи" / "лист.png").write_bytes(b"png, scanned again")
    (tmp_path / "kit_001_truth.json").unlink()
    (tmp_path / "kit_002_truth.json").write_text("{}", encoding="utf-8")
    assert sorted(verify(tmp_path, manifest)) == [
        "другой sha256: kit_001/Чертежи/лист.png",
        "нет файла: kit_001_truth.json",
        "файла нет в манифесте: kit_002_truth.json",
    ]


def test_a_manifest_reads_back_as_written(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.write(tmp_path / "manifest.json")
    assert Manifest.read(tmp_path / "manifest.json") == manifest


def test_the_committed_manifest_describes_a_whole_corpus():
    """Every scan it describes is a file it hashes, of the kits its parameters give, with the effects of each sheet."""
    path = ROOT / MANIFEST
    if not path.exists():
        pytest.skip("стрессовый корпус ещё не собран")
    manifest = Manifest.read(path)
    kits = {f"kit_{i:03d}" for i in range(1, manifest.parameters.kits + 1)}
    assert {f"{kit}_truth.json" for kit in kits} | {"corpus.json"} <= set(manifest.files)
    assert manifest.scans and set(manifest.scans) <= set(manifest.files)
    assert {path.split("/", 1)[0] for path in manifest.scans} <= kits
    assert {scan.quality for scan in manifest.scans.values()} == {"good", "fair", "poor"}
    assert all(scan.sheets and all(s.pixels[0] > 0 for s in scan.sheets) for scan in manifest.scans.values())
    assert manifest.versions["augraphy"] == "8.2.6" and manifest.environment["python"].startswith("3.11.")
