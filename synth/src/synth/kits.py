"""Synthetic kits: a random product tree drawn as consistent specifications and drawings, with planted errors.

A kit comes from its seed (ADR-0013). A random tree of assemblies (:mod:`synth.product`) gives the documents: a
specification and an assembly drawing for each assembly, a drawing for each part made with one. Mutations
(:mod:`synth.mutations`) then change the documents or their files, and each writes into the truth the discrepancies
and problem files it plants. The files are drawn by :mod:`synth.render`; a share of the drawings come out as scans.

    uv run python -m synth.kits data/synth/kits --kits 20 --seed 1

writes ``kit_001/`` … with ``kit_001_truth.json`` … next to them, and ``corpus.json`` with what each kit holds.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from synth import mutations, vocab
from synth.model import Change, Drawing, Row, Spec
from synth.product import Numbering, Part, Shape, Unit, grow
from synth.render import draw_drawing, draw_spec
from synth.scans import scan, scan_pdf
from synth.truth import DrawingTruth, ExpectedDiscrepancy, KitTruth, SpecTruth

KIT_LITERAS = ("О", "О1", "А")  # each has a stage below it, for the mutations that lower a litera
LAYOUTS = ("split", "flat", "tree")  # folders: specifications apart from drawings, none, one per assembly
NAMINGS = ("designation name", "designation", "designation_name")  # how file names are made


@dataclass
class Kit:
    """The documents of a kit before they become files, the files after, and the truth about both."""

    name: str
    seed: int
    rng: random.Random
    org: str
    litera: str
    numbering: Numbering
    root: Unit
    layout: str
    naming: str
    scan_share: float
    specs: dict[str, Spec] = field(default_factory=dict)  # by the designation of the assembly
    drawings: list[Drawing] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)  # by the path in the kit, once drawn
    expected: list[ExpectedDiscrepancy] = field(default_factory=list)
    problem_files: dict[str, str] = field(default_factory=dict)
    unread: set[str] = field(default_factory=set)  # paths of documents the check cannot read, so no fields to find
    mutations: list[str] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)  # designations a mutation has used: one mutation each
    scan_rng: random.Random = field(default_factory=random.Random)
    noise: random.Random = field(default_factory=random.Random)

    # ------------------------------------------------------------------ what mutations look up and record

    def expect(self, kind: str, designation: str, rule_id: str | None, mutation: str) -> None:
        self.expected.append(
            ExpectedDiscrepancy(type=kind, designation=designation, rule_id=rule_id, mutation=mutation)
        )

    def units(self) -> list[Unit]:
        """Assemblies whose specification is in the kit."""
        return [u for u in self.root.walk() if u.designation in self.specs]

    def drawing(self, designation: str) -> Drawing | None:
        return next((d for d in self.drawings if d.designation == designation), None)

    def free_parts(self, *, vector: bool = False) -> list[tuple[Unit, Part, Drawing]]:
        """Parts with a drawing that no mutation has used, in assemblies whose specification is in the kit."""
        found = []
        for unit in self.units():
            for part in unit.parts:
                drawing = self.drawing(part.designation)
                if drawing is None or part.designation in self.touched:
                    continue
                if vector and drawing.output != "vector":
                    continue
                found.append((unit, part, drawing))
        return found

    def path(self, unit: Unit, designation: str, name: str, suffix: str, *, specification: bool = False) -> str:
        """Where a document of ``unit`` lies in the kit, by the layout and the naming of the kit."""
        stem = {
            "designation name": f"{designation} {name}",
            "designation": designation,
            "designation_name": f"{designation}_{name.replace(' ', '_')}",
        }[self.naming]
        if self.layout == "flat":
            folder = PurePosixPath()
        elif self.layout == "split":
            folder = PurePosixPath("Спецификации" if specification else "Чертежи")
        else:
            chain = []
            node: Unit | None = unit
            while node is not None:
                chain.append(f"{node.designation} {node.name}")
                node = node.parent
            folder = PurePosixPath(*reversed(chain))
        return (folder / f"{stem}{suffix}").as_posix()

    # ------------------------------------------------------------------ documents

    def make_documents(self) -> None:
        for unit in self.root.walk():
            sb_format = "A4" if len(unit.parts) + len(unit.units) <= 3 else "A3"
            formats = {p.designation: self.rng.choice(p.kind.formats) for p in unit.parts if not p.no_drawing}
            self.specs[unit.designation] = self._spec(unit, sb_format, formats)
            self.drawings.append(self._assembly_drawing(unit, sb_format))
            for part in unit.parts:
                if not part.no_drawing:
                    self.drawings.append(self.part_drawing(unit, part, formats[part.designation]))

    def _spec(self, unit: Unit, sb_format: str, formats: dict[str, str]) -> Spec:
        rng = self.rng
        rows = [Row("Документация", vocab.FORMAT_NAMES[sb_format], "", f"{unit.designation} СБ", "Сборочный чертёж")]
        positions = iter(range(1, 1000))
        for sub in unit.units:
            rows.append(Row("Сборочные единицы", "А4", str(next(positions)), sub.designation, sub.name, str(sub.qty)))
        for part in unit.parts:
            if part.no_drawing:  # GOST 2.106 writes «БЧ» in the format column; some write it in the note as well
                fmt, note = "БЧ", "БЧ" if rng.random() < 0.5 else ""
            else:
                fmt, note = vocab.FORMAT_NAMES[formats[part.designation]], ""
            rows.append(Row("Детали", fmt, str(next(positions)), part.designation, part.name, str(part.qty), note))
        for item, qty in unit.standard:
            rows.append(Row("Стандартные изделия", "", str(next(positions)), "", item, str(qty)))
        for item, qty in unit.other:
            rows.append(Row("Прочие изделия", "", str(next(positions)), "", item, str(qty)))
        for item, amount, measure in unit.materials:
            rows.append(Row("Материалы", "", str(next(positions)), "", item, amount, measure))
        return Spec(
            self.path(unit, unit.designation, unit.name, ".pdf", specification=True),
            unit.designation,
            unit.name,
            litera=self.litera,
            changes=self._changes(),
            rows=rows,
            archive=_archive(unit.designation),
            first_use=unit.parent.designation if unit.parent else "",
        )

    def _assembly_drawing(self, unit: Unit, fmt: str) -> Drawing:
        rng = self.rng
        designation = f"{unit.designation} СБ"
        requirements = [vocab.REFERENCE.text]
        bolts = [i for i, (item, _) in enumerate(unit.standard, start=1) if item.startswith("Болт")]
        if bolts and rng.random() < 0.5:
            position = len(unit.units) + len(unit.parts) + bolts[0]
            requirements.append(f"Момент затяжки болтов поз. {position} — 45...50 Н·м.")
        positions = [str(i) for i in range(1, min(5, len(unit.units) + len(unit.parts)) + 1)]
        output = self._output()
        return Drawing(
            self.path(unit, designation, unit.name, self._suffix(output)),
            designation,
            unit.name,
            fmt=fmt,
            mass=_mass(rng, 2, 60),
            scale=rng.choice(("1:1", "1:2", "1:4")),
            litera=self.litera,
            changes=self._changes(),
            archive=_archive(unit.designation),
            first_use=unit.parent.designation if unit.parent else "",
            requirements=requirements,
            annotations=[f"Ø{rng.randrange(80, 320, 5)}", *positions],
            sketch="assembly",
            output=output,
            assembly=True,
        )

    def part_drawing(self, unit: Unit, part: Part, fmt: str) -> Drawing:
        """The drawing of ``part`` of ``unit``: its title block, technical requirements and field, and their truth."""
        rng, kind = self.rng, part.kind
        requirements = [rng.choice(kind.requirements), vocab.GENERAL_TOLERANCE] if kind.requirements else []
        if vocab.EDGES not in requirements and rng.random() < 0.3:
            requirements.append(vocab.EDGES)
        annotations = rng.sample(kind.annotations, min(len(kind.annotations), rng.randint(3, 5)))
        roughness = rng.choice(kind.general_roughness)
        params = [p for r in requirements for p in r.params] + [p for a in annotations for p in a.params]
        output = self._output()
        return Drawing(
            self.path(unit, part.designation, part.name, self._suffix(output)),
            part.designation,
            part.name,
            fmt=fmt,
            material=rng.choice(kind.materials),
            mass=_mass(rng, 0.05, 25),
            scale=rng.choice(("1:1", "1:2", "2:1")),
            litera=self.litera,
            changes=self._changes(),
            archive=_archive(part.designation),
            first_use=unit.designation,
            requirements=[r.text for r in requirements],
            annotations=[a.text for a in annotations],
            general_roughness=roughness,
            sketch=kind.sketch,
            output=output,
            expected_params=[*params, ("roughness", roughness)],
        )

    def _changes(self) -> list[Change]:
        count = self.rng.choice((0, 0, 1, 2))
        return [
            Change(n, f"{self.org}.И.{self.rng.randint(100, 999):04d}-25", f"{self.rng.randint(1, 12):02d}.25")
            for n in range(count, 0, -1)
        ]

    def _output(self) -> str:
        if self.rng.random() >= self.scan_share:
            return "vector"
        return self.rng.choice(("scan_pdf", "scan_jpg"))

    @staticmethod
    def _suffix(output: str) -> str:
        return ".jpg" if output == "scan_jpg" else ".pdf"

    # ------------------------------------------------------------------ files and truth

    def draw(self) -> None:
        """Draw every document into ``files``; scans take their skew and noise from streams of their own."""
        for spec in self.specs.values():
            self.files[spec.file] = draw_spec(spec)
        for drawing in self.drawings:
            self.files[drawing.file] = self.drawing_file(drawing)

    def drawing_file(self, drawing: Drawing) -> bytes:
        pdf = draw_drawing(drawing)
        if drawing.output == "vector":
            return pdf
        image = scan(pdf, rng=self.scan_rng, noise=self.noise)
        buf = io.BytesIO()
        if drawing.output == "scan_jpg":
            image.save(buf, "JPEG", quality=72, dpi=(150, 150))
            return buf.getvalue()
        image.save(buf, "JPEG", quality=72)
        return scan_pdf(buf.getvalue(), drawing.fmt)

    def truth(self) -> KitTruth:
        documents: dict[str, SpecTruth | DrawingTruth] = {
            s.file: SpecTruth.of(s) for s in self.specs.values() if s.file not in self.unread
        }
        documents |= {d.file: DrawingTruth.of(d) for d in self.drawings if d.file not in self.unread}
        return KitTruth(
            root=self.root.designation,
            documents=dict(sorted(documents.items())),
            discrepancies=self.expected,
            problem_files=dict(sorted(self.problem_files.items())),
            seed=self.seed,
            mutations=self.mutations,
        )

    def write(self, out: Path) -> KitTruth:
        """Write the files to ``out/<name>/`` and the truth to ``out/<name>_truth.json``."""
        folder = out / self.name
        for path, data in sorted(self.files.items()):
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        truth = self.truth()
        truth.write(out / f"{self.name}_truth.json")
        return truth


def _archive(designation: str) -> str:
    """An archive number made from the designation, as the demo kit makes them: «5311-001»."""
    return f"{designation[-10:-6]}-{designation[-3:]}"


def _mass(rng: random.Random, low: float, high: float) -> str:
    return f"{rng.uniform(low, high):.2f}".replace(".", ",")


def build(seed: int, mutation_ids: list[str], *, name: str = "kit", scan_share: float = 0.1) -> Kit:
    """A kit from ``seed`` with the mutations ``mutation_ids`` planted, those that find a target in it."""
    rng = random.Random(seed)
    depth = rng.randint(2, 4)
    # The structured scheme numbers two levels of assemblies below the product, so deeper trees use the classifier.
    scheme = "structured" if depth <= 3 and rng.random() < 0.5 else "classifier"
    org = rng.choice(vocab.ORG_CODES)
    numbering = Numbering(org, scheme, rng)
    root = grow(rng, numbering, Shape(depth))
    kit = Kit(
        name,
        seed,
        rng,
        org,
        rng.choice(KIT_LITERAS),
        numbering,
        root,
        rng.choice(LAYOUTS),
        rng.choice(NAMINGS),
        scan_share,
        scan_rng=random.Random(seed + 1),
        noise=random.Random(seed + 2),
    )
    kit.make_documents()
    planned = [mutations.CATALOG[m] for m in mutation_ids]
    for mutation in planned:
        if mutation.stage == "documents" and mutation.plant(kit):
            kit.mutations.append(mutation.id)
    kit.draw()
    for mutation in planned:
        if mutation.stage == "files" and mutation.plant(kit):
            kit.mutations.append(mutation.id)
    return kit


def corpus(
    out: Path,
    kits: int,
    seed: int,
    *,
    per_kit: tuple[int, int] = (2, 6),
    clean_every: int = 5,
    scan_share: float = 0.1,
) -> list[KitTruth]:
    """``kits`` kits from ``seed``: every ``clean_every``-th one clean, the others with mutations in turn.

    The mutations come from the catalog shuffled and taken in turn, so each one is planted about as often.
    """
    rng = random.Random(seed)
    queue: list[str] = []
    truths = []
    summary = []
    for index in range(1, kits + 1):
        ids: list[str] = []
        if index % clean_every:
            for _ in range(rng.randint(*per_kit)):
                if not queue:
                    queue = list(mutations.CATALOG)
                    rng.shuffle(queue)
                if (candidate := queue.pop()) not in ids:
                    ids.append(candidate)
        kit = build(seed * 1000 + index, ids, name=f"kit_{index:03d}", scan_share=scan_share)
        truths.append(kit.write(out))
        summary.append(
            {
                "kit": kit.name,
                "seed": kit.seed,
                "mutations": kit.mutations,
                "files": len(kit.files),
                "documents": len(truths[-1].documents),
                "discrepancies": len(kit.expected),
                "problem_files": len(kit.problem_files),
            }
        )
    manifest = {"generator": "synth.kits", "seed": seed, "kits": summary}
    (out / "corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return truths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m synth.kits", description="Синтетические комплекты КД с ошибками")
    parser.add_argument("out", help="папка корпуса, например data/synth/kits")
    parser.add_argument("--kits", type=int, default=20, help="число комплектов")
    parser.add_argument("--seed", type=int, default=1, help="сид корпуса")
    parser.add_argument("--clean-every", type=int, default=5, help="каждый n-й комплект — без ошибок")
    parser.add_argument("--scan-share", type=float, default=0.1, help="доля чертежей-сканов")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    truths = corpus(out, args.kits, args.seed, clean_every=args.clean_every, scan_share=args.scan_share)
    planted = sum(len(t.discrepancies) for t in truths)
    print(f"Комплектов: {len(truths)}, заложено расхождений: {planted}. Корпус: {out}", file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
