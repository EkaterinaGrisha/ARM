"""The held-out corpus: built once from a committed generator, frozen by its manifest, never built too small."""

import pytest

from synth import heldout


@pytest.fixture
def committed(monkeypatch):
    monkeypatch.setattr(heldout, "commit_of_generator", lambda: "0" * 40)


def test_the_manifest_freezes_every_file_and_tells_what_was_planted(tmp_path, committed):
    out = tmp_path / "heldout"
    manifest = heldout.build(out, heldout.Parameters(kits=3, seed=5, clean_every=2, min_planted=0))
    assert manifest.commit == "0" * 40 and manifest.parameters.kits == 3
    assert "corpus.json" in manifest.files and any(p.endswith("_truth.json") for p in manifest.files)
    assert sum(manifest.planted.values()) == sum(manifest.rules.values()) > 0
    path = tmp_path / "manifest.json"
    manifest.write(path)
    assert heldout.verify(out, heldout.Manifest.read(path)) == []
    changed = next(p for p in sorted(out.rglob("*.pdf")))
    changed.write_bytes(changed.read_bytes() + b"%\n")
    (out / "extra.pdf").write_bytes(b"%PDF-1.7\n")
    problems = heldout.verify(out, manifest)
    assert f"другой sha256: {changed.relative_to(out).as_posix()}" in problems
    assert "файла нет в манифесте: extra.pdf" in problems


def test_a_corpus_too_small_for_a_mandatory_type_is_refused(tmp_path, committed):
    with pytest.raises(SystemExit, match="нужно больше комплектов"):
        heldout.build(tmp_path / "small", heldout.Parameters(kits=2, seed=5, min_planted=30))


def test_a_corpus_is_built_into_an_empty_folder_only(tmp_path, committed):
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "kit_001").mkdir()
    with pytest.raises(SystemExit, match="не пуста"):
        heldout.build(tmp_path / "used", heldout.Parameters(kits=1, min_planted=0))
