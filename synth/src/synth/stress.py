"""The stress corpus: kits of :mod:`synth.kits` whose scans go through Augraphy, built once and frozen (ADR-0013).

The kits, their documents and rasters, and the seed of each scan are those of :func:`synth.kits.corpus` with the same
parameters; only the scans differ (:mod:`synth.augraphy_scans`). So each scan pairs with the scan of the same document
by :mod:`synth.degrade`, of the same class and with the same scanner settings. The defaults are the parameters of the
corpus of ``eval/scan_quality.py``.

The manifest freezes the corpus: the sha256 of every file, the versions of Python and of the libraries the pixels
depend on, the sha256 of the generator, the parameters, and the seed, settings and effects of every scan, sheet by
sheet. It goes into git; the files stay in ``data/``. A build on the same platform with the same versions gives the
same bytes, and checking the files needs no Augraphy:

    uv run --group augraphy python -m synth.stress build data/synth/stress
    uv run python -m synth.stress verify data/synth/stress
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from synth import kits
from synth.truth import AugraphyCapture, DrawingTruth, SpecTruth

MANIFEST = Path("eval") / "stress_corpus.json"  # from the root of the repository, where the commands run
SOURCE = Path(__file__).parent  # the generator: the modules and fonts of synth
# what the pixels depend on: Augraphy and what it computes with, the renderer and the encoder of the files
LIBRARIES = (
    "augraphy",
    "numba",
    "llvmlite",
    "numpy",
    "opencv-python",
    "scikit-image",
    "scikit-learn",
    "scipy",
    "matplotlib",
    "pillow",
    "pypdfium2",
    "reportlab",
    "pydantic",
)


class Parameters(BaseModel):
    """What :func:`synth.kits.corpus` builds the kits with."""

    model_config = ConfigDict(extra="forbid")

    kits: int = 6
    seed: int = 3
    clean_every: int = 1
    scan_share: float = 0.45
    raster_share: float = 0.3


class Manifest(BaseModel):
    """What the stress corpus is, how it was built and what each of its files hashes to."""

    model_config = ConfigDict(extra="forbid")

    generator: str = "synth.stress"
    built: str  # the date of the build, UTC
    parameters: Parameters
    environment: dict[str, str]  # Python, the platform, the threads of numba
    versions: dict[str, str]  # of LIBRARIES
    source: str  # sha256 of the generator, by source_digest
    files: dict[str, str]  # path in the corpus → sha256
    scans: dict[str, AugraphyCapture]  # path of a scan → its seed, settings and the effects of each sheet

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> Manifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def build(out: Path, parameters: Parameters) -> Manifest:
    """The corpus built into the empty folder ``out``, and its manifest."""
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Augraphy работает только на Python 3.11 (ADR-0013): стрессовый корпус собирается на нём")
    try:
        from synth import augraphy_scans
    except ImportError as error:
        raise SystemExit("Нужна группа augraphy: uv run --group augraphy python -m synth.stress build …") from error
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
        capture=augraphy_scans.capture,
    )
    scans = {
        f"kit_{index:03d}/{path}": doc.capture
        for index, truth in enumerate(truths, start=1)
        for path, doc in truth.documents.items()
        if isinstance(doc, SpecTruth | DrawingTruth) and isinstance(doc.capture, AugraphyCapture)
    }
    return Manifest(
        built=datetime.now(UTC).date().isoformat(),
        parameters=parameters,
        environment={"python": platform.python_version(), "platform": platform.platform(), "numba_threads": "1"},
        versions={name: importlib.metadata.version(name) for name in LIBRARIES},
        source=source_digest(),
        files=digests(out),
        scans=dict(sorted(scans.items())),
    )


def verify(out: Path, manifest: Manifest) -> list[str]:
    """What in ``out`` does not match ``manifest``: files missing, other bytes, files it does not list; none if all
    match."""
    found = digests(out)
    problems = [f"нет файла: {path}" for path in manifest.files if path not in found]
    problems += [f"другой sha256: {path}" for path, d in manifest.files.items() if path in found and found[path] != d]
    problems += [f"файла нет в манифесте: {path}" for path in found if path not in manifest.files]
    return problems


def digests(out: Path) -> dict[str, str]:
    """sha256 of every file under ``out`` by its path there; hidden files, such as the folder notes of an OS, aside."""
    files = (p for p in sorted(out.rglob("*")) if p.is_file() and not _hidden(p.relative_to(out)))
    return {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def source_digest() -> str:
    """sha256 of the generator: each module and font of synth, by its path and its sha256."""
    digest = hashlib.sha256()
    for path in sorted(SOURCE.rglob("*")):
        relative = path.relative_to(SOURCE)
        if path.is_file() and "__pycache__" not in relative.parts and not _hidden(relative):
            digest.update(relative.as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _hidden(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m synth.stress", description="Стрессовый корпус сканов Augraphy")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build", help="собрать корпус и записать манифест (Python 3.11, группа augraphy)")
    check = commands.add_parser("verify", help="сверить файлы корпуса с манифестом")
    defaults = Parameters()
    make.add_argument("--kits", type=int, default=defaults.kits, help="число комплектов")
    make.add_argument("--seed", type=int, default=defaults.seed, help="сид корпуса")
    make.add_argument("--clean-every", type=int, default=defaults.clean_every, help="каждый n-й комплект — без ошибок")
    make.add_argument("--scan-share", type=float, default=defaults.scan_share, help="доля документов-сканов")
    make.add_argument("--raster-share", type=float, default=defaults.raster_share, help="доля растров без сканирования")
    for command in (make, check):
        command.add_argument("out", type=Path, help="папка корпуса, например data/synth/stress")
        command.add_argument("--manifest", type=Path, default=MANIFEST, help=f"манифест, по умолчанию {MANIFEST}")
    args = parser.parse_args(argv)
    if args.command == "build":
        parameters = Parameters(
            kits=args.kits,
            seed=args.seed,
            clean_every=args.clean_every,
            scan_share=args.scan_share,
            raster_share=args.raster_share,
        )
        manifest = build(args.out, parameters)
        manifest.write(args.manifest)
        sheets = sum(len(scan.sheets) for scan in manifest.scans.values())
        print(f"Комплектов: {parameters.kits}, сканов Augraphy: {len(manifest.scans)}, листов: {sheets}.")
        print(f"Корпус: {args.out}, файлов: {len(manifest.files)}. Манифест: {args.manifest}")
        return 0
    manifest = Manifest.read(args.manifest)
    problems = verify(args.out, manifest)
    for problem in problems:
        print(problem, file=sys.stderr)
    if manifest.source != source_digest():
        print("Генератор изменился после сборки корпуса: документы могут не совпасть с комплектами synth.kits")
    if problems:
        print(f"Корпус {args.out} не совпадает с манифестом {args.manifest}: {len(problems)}", file=sys.stderr)
        return 1
    print(f"Корпус {args.out} совпадает с манифестом {args.manifest}: файлов {len(manifest.files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
