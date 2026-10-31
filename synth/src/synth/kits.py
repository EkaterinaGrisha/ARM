"""Synthetic kits: a random product tree drawn as consistent specifications and drawings, with planted errors.

A kit comes from its seed (ADR-0013). A random tree of assemblies (:mod:`synth.product`) gives the documents: a
specification and an assembly drawing for each assembly, a drawing for each part made with one. Items in variants get
group documents of GOST 2.113: one drawing with a table of variants for all the variants of a part, and for a group
assembly a group specification by variant A or B and a group assembly drawing. Technical requirements come from
templates (:mod:`synth.requirements`). A share of the kits is drawn in the lettering of type B, the rest in type A.
Mutations (:mod:`synth.mutations`) then change the documents or their files, and each writes into the truth the
discrepancies and problem files it plants; they leave group documents alone. The files are drawn by
:mod:`synth.render`; a share of the documents come out as rasters or scans of one of three classes of quality
(:mod:`synth.degrade`), each with a seed of its own in the truth. The stress corpus scans the same kits through
Augraphy instead (:mod:`synth.stress`).

    uv run python -m synth.kits data/synth/kits --kits 20 --seed 1

writes ``kit_001/`` … with ``kit_001_truth.json`` … next to them, and ``corpus.json`` with what each kit holds.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from synth import degrade, mutations, requirements, vocab
from synth.model import Change, Container, Drawing, FontType, Group, Kind, Output, Quality, Row, Spec, Variant
from synth.product import Numbering, Part, Shape, Unit, grow
from synth.render import GROUP_NAME_WIDTH, NAME_WIDTH, Box, draw_drawing, draw_spec, text_width, wrap
from synth.truth import CaptureRecord, DrawingTruth, ExpectedDiscrepancy, KitTruth, SpecTruth
from synth.vocab import Param

KIT_LITERAS = ("О", "О1", "А")  # each has a stage below it, for the mutations that lower a litera
LAYOUTS = ("split", "flat", "tree")  # folders: specifications apart from drawings, none, one per assembly
NAMINGS = ("designation name", "designation", "designation_name")  # how file names are made
QUALITIES: tuple[Quality, ...] = ("good", "fair", "poor")  # classes of a raster or a scan, equally often
TYPE_B_SHARE = 0.5  # of the kits drawn in the lettering of type B
# what a scanner writes, and what CAD exports to pixels; a picture holds one page
SCAN_CONTAINERS: tuple[Container, ...] = ("pdf", "pdf", "pdf", "jpg", "tiff")
RASTER_CONTAINERS: tuple[Container, ...] = ("pdf", "pdf", "png", "tiff", "jpg")
SUFFIXES = {"pdf": ".pdf", "jpg": ".jpg", "png": ".png", "tiff": ".tif"}


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
    raster_share: float
    specs: dict[str, Spec] = field(default_factory=dict)  # by the designation of the assembly
    drawings: list[Drawing] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)  # by the path in the kit, once drawn
    expected: list[ExpectedDiscrepancy] = field(default_factory=list)
    problem_files: dict[str, str] = field(default_factory=dict)
    unread: set[str] = field(default_factory=set)  # paths of documents the check cannot read, so no fields to find
    drawn: dict[str, dict[str, Box]] = field(default_factory=dict)  # where the values of each file are drawn
    captures: dict[str, CaptureRecord] = field(default_factory=dict)  # how the rasters and scans were made, by path
    mutations: list[str] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)  # designations a mutation has used: one mutation each
    scan_rng: random.Random = field(default_factory=random.Random)  # the seed of each raster and scan
    capture: degrade.Capturer = degrade.capture  # what makes the rasters and scans
    font_type: FontType = "A"  # the lettering of GOST 2.304 all documents of the kit are drawn in

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

    def free_units(self) -> list[Unit]:
        """Assemblies whose specification is in the kit, single ones that no mutation has used."""
        return [u for u in self.units() if u.designation not in self.touched and not u.variants]

    def free_parts(self, *, vector: bool = False) -> list[tuple[Unit, Part, Drawing]]:
        """Parts with a drawing of their own that no mutation has used, in assemblies whose specification is in the
        kit: a group drawing covers several designations, and the mutations plant errors in one."""
        found = []
        for unit in self.units():
            for part in unit.parts:
                drawing = self.drawing(part.designation)
                if drawing is None or part.group or part.designation in self.touched:
                    continue
                if vector and drawing.output.kind != "vector":
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
            # a group document takes A3 for its table of variants
            sb_format = "A4" if len(unit.parts) + len(unit.units) <= 3 and not unit.variants else "A3"
            formats = {
                p.designation: "A3" if p.group else self.rng.choice(p.kind.formats)
                for p in unit.parts
                if not p.no_drawing
            }
            spec = self._spec(unit, sb_format, formats)
            self.specs[unit.designation] = spec
            self.drawings.append(self._assembly_drawing(unit, sb_format, spec))
            groups: dict[str, list[Part]] = {}
            for part in unit.parts:
                if part.group:
                    groups.setdefault(part.group, []).extend([part, *part.alternatives])
                elif not part.no_drawing:
                    self.drawings.append(self.part_drawing(unit, part, formats[part.designation]))
            for members in groups.values():
                self.drawings.append(self.part_drawing(unit, members[0], "A3", variants=members))

    def _spec(self, unit: Unit, sb_format: str, formats: dict[str, str]) -> Spec:
        """The specification of ``unit``; of a group assembly, a group one by variant A or B of GOST 2.113."""
        group: Group = ("A" if self.rng.random() < 0.5 else "B") if unit.variants else ""
        width = GROUP_NAME_WIDTH if group == "B" else NAME_WIDTH

        def named(item: str) -> str:  # long names take several lines, as a CAD system wraps them
            return wrap(item, width, font=self.font_type)

        positions = (str(n) for n in itertools.count(1))
        rows = [Row("Документация", vocab.FORMAT_NAMES[sb_format], "", f"{unit.designation} СБ", "Сборочный чертёж")]
        for sub in unit.units:
            rows += [Row("Сборочные единицы", "А4", next(positions), d, sub.name, str(sub.qty)) for d in sub.held]
        variable = []  # variant A: the parts that the variants hold in variants of their own
        for part in unit.parts:
            if part.alternatives and group == "A":
                variable.append(part)
                continue
            rows.append(self._part_row(part, formats, next(positions), named))
            # variant B: the variants of a part share its position, each in its column of quantities
            rows += [self._part_row(p, formats, "", named) for p in part.alternatives]
        standard = [(item, [qty]) for item, qty in unit.standard]
        if group == "B":
            standard = sorted(standard + unit.varied)
        for item, quantities in standard:
            rows.append(Row("Стандартные изделия", "", next(positions), "", named(item), str(quantities[0])))
        for item, qty in unit.other:
            rows.append(Row("Прочие изделия", "", next(positions), "", named(item), str(qty)))
        for item, amount, measure in unit.materials:
            rows.append(Row("Материалы", "", next(positions), "", named(item), amount, measure))
        if group == "A":
            rows += self._variable_data(unit, variable, formats, positions, named)
        elif group == "B":
            _by_variant(rows, unit)
        if group:
            _shorten(rows)
        output = self._output(pages=None)
        return Spec(
            self.path(unit, unit.designation, unit.name, SUFFIXES[output.container], specification=True),
            unit.designation,
            unit.name,
            litera=self.litera,
            changes=self._changes(),
            rows=rows,
            archive=_archive(unit.designation),
            first_use=unit.parent.designation if unit.parent else "",
            output=output,
            font_type=self.font_type,
            group=group,
            variants=list(unit.variants),
        )

    def _part_row(
        self, part: Part, formats: dict[str, str], pos: str, named: Callable[[str], str], variant: str = ""
    ) -> Row:
        if part.no_drawing:  # GOST 2.106 writes «БЧ» in the format column; some write it in the note as well
            fmt, note = "БЧ", "БЧ" if self.rng.random() < 0.5 else ""
        else:  # the variants of a part are on its group drawing
            fmt, note = vocab.FORMAT_NAMES[formats[part.group or part.designation]], ""
        return Row("Детали", fmt, pos, part.designation, named(part.name), str(part.qty), note, variant=variant)

    def _variable_data(
        self,
        unit: Unit,
        parts: list[Part],
        formats: dict[str, str],
        positions: Iterator[str],
        named: Callable[[str], str],
    ) -> list[Row]:
        """Variant A, GOST 2.113, 5.5.1: after the constant data, what each variant holds, under its designation.
        The items of the base variant go on with the positions, and the other variants hold theirs in the same ones."""
        numbers = [next(positions) for _ in parts]
        standard = [(item, quantities, next(positions)) for item, quantities in unit.varied]
        rows = []
        for k, variant in enumerate(unit.variants):
            for part, pos in zip(parts, numbers, strict=True):
                rows.append(self._part_row(part.alternatives[k - 1] if k else part, formats, pos, named, variant))
            for item, quantities, pos in standard:
                rows.append(Row("Стандартные изделия", "", pos, "", named(item), str(quantities[k]), variant=variant))
        return rows

    def _assembly_drawing(self, unit: Unit, fmt: str, spec: Spec) -> Drawing:
        rng = self.rng
        designation = f"{unit.designation} СБ"

        def position(word: str) -> str | None:  # of the first purchased item or material whose name starts so
            return next((r.pos for r in spec.rows if r.pos and not r.designation and r.name.startswith(word)), None)

        bearings = any(r.name.startswith("Подшипник") for r in spec.rows)
        items = requirements.assembly(rng, position("Болт"), bearings, position("Масло"))
        positions = [str(i) for i in range(1, min(5, len(unit.units) + len(unit.parts)) + 1)]
        output = self._output(pages=1)
        mass = _mass(rng, 2, 60)
        variants = []
        if unit.variants:  # a group assembly drawing: the mass of each variant in its table
            masses = [mass, *(_mass(rng, 2, 60) for _ in unit.variants[1:])]
            variants = [Variant(v, _suffix(v, unit.designation), m) for v, m in zip(unit.variants, masses, strict=True)]
            mass = SEE_TABLE
        return Drawing(
            self.path(unit, designation, unit.name, SUFFIXES[output.container]),
            designation,
            unit.name,
            fmt=fmt,
            mass=mass,
            scale=rng.choice(("1:1", "1:2", "1:4")),
            litera=self.litera,
            changes=self._changes(),
            archive=_archive(unit.designation),
            first_use=unit.parent.designation if unit.parent else "",
            requirements=self._wrapped(items),
            annotations=[f"Ø{rng.randrange(80, 320, 5)}", *positions],
            sketch="assembly",
            output=output,
            assembly=True,
            font_type=self.font_type,
            variants=variants,
        )

    def _wrapped(self, items: list[requirements.Requirement]) -> list[str]:
        """The texts of ``items`` with line breaks, each line within the 185 mm of the column after its number."""
        width = 185 - text_width(f"{len(items)}. ", font=self.font_type)
        return [wrap(item.text, width, font=self.font_type) for item in items]

    def part_drawing(self, unit: Unit, part: Part, fmt: str, variants: list[Part] | None = None) -> Drawing:
        """The drawing of ``part`` of ``unit``: its title block, technical requirements and field, and their truth.

        With ``variants``, a group drawing of all of them: the field gives the letter of the dimension that changes,
        the table of variants its value and the mass of each variant, and the title block «См. табл.» for the mass.
        """
        rng, kind = self.rng, part.kind
        material = rng.choice(kind.materials)
        items = requirements.part(rng, kind, material)
        annotations = rng.sample(kind.annotations, min(len(kind.annotations), rng.randint(3, 5)))
        roughness = rng.choice(kind.general_roughness)
        keyed = [(p, f"requirement:{i}") for i, r in enumerate(items) for p in r.params]
        keyed += [(p, f"annotation:{j}") for j, a in enumerate(annotations) for p in a.params]
        keyed.append((Param("roughness", roughness), "general_roughness"))
        more_sheets: list[list[str]] = []
        if rng.random() < 0.15:
            for number in range(2, 2 + (1 if rng.random() < 0.7 else 2)):
                notes = rng.sample(kind.annotations, min(len(kind.annotations), rng.randint(2, 3)))
                keyed += [(p, f"sheet{number}:annotation:{j}") for j, a in enumerate(notes) for p in a.params]
                more_sheets.append([a.text for a in notes])
        output = self._output(pages=1 + len(more_sheets))
        mass = _mass(rng, 0.05, 25)
        texts, table = [a.text for a in annotations], []
        if variants:
            letter, low, high = kind.variable
            values = sorted(rng.sample(range(low, high + 1, 5), len(variants)))
            base = float(mass.replace(",", "."))
            for member, value in zip(variants, values, strict=True):
                weight = f"{base * value / values[0]:.2f}".replace(".", ",")
                table.append(
                    Variant(
                        member.designation, _suffix(member.designation, part.designation), weight, {letter: str(value)}
                    )
                )
            texts.append(letter)
            mass = SEE_TABLE
        return Drawing(
            self.path(unit, part.designation, part.name, SUFFIXES[output.container]),
            part.designation,
            part.name,
            fmt=fmt,
            material=material.text,
            mass=mass,
            scale=rng.choice(("1:1", "1:2", "2:1")),
            litera=self.litera,
            changes=self._changes(),
            archive=_archive(part.designation),
            first_use=unit.designation,
            requirements=self._wrapped(items),
            annotations=texts,
            general_roughness=roughness,
            sketch=kind.sketch,
            output=output,
            expected_params=[p for p, _ in keyed],
            param_keys=[k for _, k in keyed],
            more_sheets=more_sheets,
            font_type=self.font_type,
            variants=table,
        )

    def _changes(self) -> list[Change]:
        count = self.rng.choice((0, 0, 1, 2))
        return [
            Change(n, f"{self.org}.И.{self.rng.randint(100, 999):04d}-25", f"{self.rng.randint(1, 12):02d}.25")
            for n in range(count, 0, -1)
        ]

    def _output(self, pages: int | None) -> Output:
        """Vector, or by the shares of the kit a scan or a raster of a class at random; a picture only for one page,
        and ``pages`` is none for a specification, which is paginated as it is drawn."""
        roll = self.rng.random()
        if roll >= self.scan_share + self.raster_share:
            return Output()
        kind: Kind = "scan" if roll < self.scan_share else "raster"
        containers = SCAN_CONTAINERS if kind == "scan" else RASTER_CONTAINERS
        if pages != 1:
            containers = tuple(c for c in containers if c in ("pdf", "tiff"))
        return Output(kind, self.rng.choice(containers), self.rng.choice(QUALITIES))

    # ------------------------------------------------------------------ files and truth

    def draw(self) -> None:
        """Draw every document into ``files``; rasters and scans take their seeds from a stream of their own."""
        for spec in self.specs.values():
            boxes = self.drawn.setdefault(spec.file, {})
            self.files[spec.file] = self.pixels(spec.file, draw_spec(spec, boxes=boxes), spec.output, boxes)
        for drawing in self.drawings:
            boxes = self.drawn.setdefault(drawing.file, {})
            pdf = draw_drawing(drawing, boxes=boxes)
            self.files[drawing.file] = self.pixels(drawing.file, pdf, drawing.output, boxes)

    def pixels(self, path: str, pdf: bytes, output: Output, boxes: dict[str, Box]) -> bytes:
        """``pdf`` as ``output`` asks: as is, or captured by the degrader of the kit, boxes moved with the sheets."""
        if output.kind == "vector":
            return pdf
        data, self.captures[path] = self.capture(pdf, output, self.scan_rng.getrandbits(32), boxes)
        return data

    def truth(self) -> KitTruth:
        documents: dict[str, SpecTruth | DrawingTruth] = {
            s.file: SpecTruth.of(s, self.drawn.get(s.file), self.captures.get(s.file))
            for s in self.specs.values()
            if s.file not in self.unread
        }
        documents |= {
            d.file: DrawingTruth.of(d, self.drawn.get(d.file), self.captures.get(d.file))
            for d in self.drawings
            if d.file not in self.unread
        }
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


SEE_TABLE = "См. табл."  # what a group drawing writes for a mass that changes from variant to variant, GOST 2.113


def _suffix(designation: str, base: str) -> str:
    """How a table of variants writes ``designation`` below the base one: «-01»; the base one in full."""
    return designation.removeprefix(base) if designation != base else ""


def _by_variant(rows: list[Row], unit: Unit) -> None:
    """Variant B, GOST 2.113, 5.5.2: the quantity of each row on each variant in a column of its own, «×» for a
    document. A part of one variant stands in its column only; a standard item may stand in each by another number.
    The single column of quantities stays empty."""
    held = {
        p.designation: unit.variants[k]
        for part in unit.parts
        if part.alternatives
        for k, p in enumerate([part, *part.alternatives])
    }
    varied = dict(unit.varied)
    for row in rows:
        if row.section == "Документация":
            row.quantities = dict.fromkeys(unit.variants, "×")
        elif row.designation in held:
            row.quantities = {held[row.designation]: row.qty}
        elif (name := row.name.replace("\n", " ")) in varied and not row.designation:
            row.quantities = dict(zip(unit.variants, map(str, varied[name]), strict=True))
        else:
            row.quantities = dict.fromkeys(unit.variants, row.qty)
        row.qty = ""


def _shorten(rows: list[Row]) -> None:
    """GOST 2.113, 5.3: in a group specification a row right below one of the same base designation, in the same
    section of the same data, writes only the number of its variant, «-01», and leaves the format to the full row."""
    previous = ("", "", "")
    for row in rows:
        base = re.sub(r"-\d{2}$", "", row.designation)
        if row.designation and (base, row.section, row.variant) == previous and row.designation != base:
            row.written, row.fmt = row.designation.removeprefix(base), ""
        elif row.designation:
            previous = (base, row.section, row.variant)


def _archive(designation: str) -> str:
    """An archive number made from the designation, as the demo kit makes them: «5311-001»."""
    return f"{designation[-10:-6]}-{designation[-3:]}"


def _mass(rng: random.Random, low: float, high: float) -> str:
    return f"{rng.uniform(low, high):.2f}".replace(".", ",")


def build(
    seed: int,
    mutation_ids: list[str],
    *,
    name: str = "kit",
    scan_share: float = 0.1,
    raster_share: float = 0.05,
    capture: degrade.Capturer = degrade.capture,
    type_b_share: float = TYPE_B_SHARE,
) -> Kit:
    """A kit from ``seed`` with the mutations ``mutation_ids`` planted, those that find a target in it.

    ``scan_share`` and ``raster_share`` of the documents come out as scans and as rasters, made by ``capture``; a kit
    is drawn in the lettering of type B with the chance ``type_b_share``, which leaves the rest of the kit as it is.
    """
    rng = random.Random(seed)
    depth = rng.randint(2, 4)
    # The structured scheme numbers two levels of assemblies below the product, so deeper trees use the classifier.
    scheme = "structured" if depth <= 3 and rng.random() < 0.5 else "classifier"
    org = rng.choice(vocab.ORG_CODES)
    numbering = Numbering(org, scheme, rng)
    root = grow(rng, numbering, Shape(depth))
    litera, layout, naming = rng.choice(KIT_LITERAS), rng.choice(LAYOUTS), rng.choice(NAMINGS)
    font_type: FontType = "B" if rng.random() < type_b_share else "A"
    kit = Kit(
        name,
        seed,
        rng,
        org,
        litera,
        numbering,
        root,
        layout,
        naming,
        scan_share,
        raster_share,
        scan_rng=random.Random(seed + 1),
        capture=capture,
        font_type=font_type,
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
    raster_share: float = 0.05,
    capture: degrade.Capturer = degrade.capture,
) -> list[KitTruth]:
    """``kits`` kits from ``seed``: every ``clean_every``-th one clean, the others with mutations in turn.

    The mutations come from the catalog shuffled and taken in turn, so each one is planted about as often. The
    rasters and scans are made by ``capture``; the kits and the seeds of their documents do not depend on it.
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
        kit = build(
            seed * 1000 + index,
            ids,
            name=f"kit_{index:03d}",
            scan_share=scan_share,
            raster_share=raster_share,
            capture=capture,
        )
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
                "captures": dict(sorted(Counter(c.group for c in kit.captures.values()).items())),
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
    parser.add_argument("--scan-share", type=float, default=0.1, help="доля документов-сканов")
    parser.add_argument("--raster-share", type=float, default=0.05, help="доля растровых документов без сканирования")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    truths = corpus(
        out,
        args.kits,
        args.seed,
        clean_every=args.clean_every,
        scan_share=args.scan_share,
        raster_share=args.raster_share,
    )
    planted = sum(len(t.discrepancies) for t in truths)
    print(f"Комплектов: {len(truths)}, заложено расхождений: {planted}. Корпус: {out}", file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
