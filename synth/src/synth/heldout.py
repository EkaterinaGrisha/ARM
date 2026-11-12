"""The held-out corpus: synthetic kits for one measurement after the rules freeze, never for tuning (ADR-0013).

The kits of ``eval/synth_kits.py`` with seed 1 tune the rules, so their numbers are numbers of tuning. This corpus is
built the same way, by :func:`synth.kits.corpus` with the same shares of scans, rasters and kits kept one sheet per
file, but from a seed of its own and with as many kits as it takes for each mandatory type of discrepancy of the case
to be planted at least ``min_planted`` times. It is built once, from a generator committed to git, and frozen by its
manifest: the sha256 of every file, the parameters, the commit and the sha256 of the generator, the versions the files
depend on, and what was planted. The manifest goes into git, the files stay in ``data/``; the core runs on the corpus
once, with ``arm-2`` and ``arm-3`` (step 6 of the plan).

    uv run python -m synth.heldout build data/synth/heldout
    uv run python -m synth.heldout verify data/synth/heldout
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from synth import kits
from synth.stress import SOURCE, digests, source_digest

MANIFEST = Path("eval") / "heldout_corpus.json"  # from the root of the repository, where the commands run
MANDATORY = ("missing_drawing", "orphan_drawing", "name_mismatch", "duplicate_designation", "revision_mismatch")
LIBRARIES = ("pillow", "numpy", "pypdfium2", "reportlab", "pydantic")  # what the files of the kits depend on


class Parameters(BaseModel):
    """What :func:`synth.kits.corpus` builds the kits with: the shares of ``eval/synth_kits.py``, another seed."""

    model_config = ConfigDict(extra="forbid")

    kits: int = 120  # with 100, name_mismatch, the rarest mandatory type, would get only 31
    seed: int = 11
    clean_every: int = 5
    scan_share: float = 0.1
    raster_share: float = 0.05
    sheet_files: float = 0.5
    min_planted: int = 30  # of each mandatory type


class Manifest(BaseModel):
    """What the held-out corpus is, how it was built and what each of its files hashes to."""

    model_config = ConfigDict(extra="forbid")

    generator: str = "synth.heldout"
    built: str  # the date of the build, UTC
    parameters: Parameters
    commit: str  # of the repository, whose generator is unchanged in the working tree
    environment: dict[str, str]
    versions: dict[str, str]
    source: str  # sha256 of the generator, by source_digest
    planted: dict[str, int]  # discrepancies by type
    rules: dict[str, int]  # discrepancies by the rule of the core that finds them, «—» for none
    problem_files: dict[str, int]  # by the status to expect
    files: dict[str, str]  # path in the corpus → sha256

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> Manifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def commit_of_generator() -> str:
    """The commit of the repository, refused when the generator differs from it in the working tree: the corpus is
    built by a generator that git can give back."""
    root = SOURCE.parents[2]
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", str(SOURCE)], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    if changed.strip():
        raise SystemExit(f"Генератор изменён и не закоммичен:\n{changed}Корпус собирается из закоммиченного генератора")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def build(out: Path, parameters: Parameters) -> Manifest:
    """The corpus built into the empty folder ``out``, and its manifest; refused when a mandatory type has fewer than
    ``min_planted`` discrepancies, so the parameters take more kits."""
    commit = commit_of_generator()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"Папка {out} не пуста: корпус собирается в пустую папку")
    out.mkdir(parents=True, exist_ok=True)
    truths = kits.corpus(
        out,
        parameters.kits,
        parameters.seed,
        clean_every=parameters.clean_every,
        scan_share=parameters.scan_share,
        raster_share=parameters.raster_share,
        sheet_files_share=parameters.sheet_files,
    )
    planted = Counter(d.type for t in truths for d in t.discrepancies)
    short = {kind: planted[kind] for kind in MANDATORY if planted[kind] < parameters.min_planted}
    if short:
        raise SystemExit(f"Заложено меньше {parameters.min_planted} расхождений типов {short}: нужно больше комплектов")
    return Manifest(
        built=datetime.now(UTC).date().isoformat(),
        parameters=parameters,
        commit=commit,
        environment={"python": platform.python_version(), "platform": platform.platform()},
        versions={name: importlib.metadata.version(name) for name in LIBRARIES},
        source=source_digest(),
        planted=dict(sorted(planted.items())),
        rules=dict(sorted(Counter(d.rule_id or "—" for t in truths for d in t.discrepancies).items())),
        problem_files=dict(sorted(Counter(s for t in truths for s in t.problem_files.values()).items())),
        files=digests(out),
    )


def verify(out: Path, manifest: Manifest) -> list[str]:
    """What in ``out`` does not match ``manifest``: files missing, other bytes, files it does not list; none if all
    match."""
    found = digests(out) if out.is_dir() else {}
    problems = [f"нет файла: {path}" for path in manifest.files if path not in found]
    problems += [f"другой sha256: {path}" for path, d in manifest.files.items() if path in found and found[path] != d]
    problems += [f"файла нет в манифесте: {path}" for path in found if path not in manifest.files]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("build", "собрать корпус в пустую папку"), ("verify", "сверить файлы с манифестом")):
        command = sub.add_parser(name, help=text)
        command.add_argument("out", type=Path)
        command.add_argument("--manifest", type=Path, default=MANIFEST, help=f"манифест, по умолчанию {MANIFEST}")
    args = parser.parse_args(argv)
    if args.command == "build":
        manifest = build(args.out, Parameters())
        manifest.write(args.manifest)
        planted = ", ".join(f"{kind} {n}" for kind, n in manifest.planted.items())
        print(f"Корпус: {len(manifest.files)} файлов, {manifest.parameters.kits} комплектов; заложено: {planted}")
        print(f"Манифест: {args.manifest}")
        return 0
    problems = verify(args.out, Manifest.read(args.manifest))
    for problem in problems:
        print(problem)
    print("Файлы совпадают с манифестом" if not problems else f"Расхождений с манифестом: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
