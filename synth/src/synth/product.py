"""A random product tree: assemblies with their own assemblies, parts and purchased items, and their designations.

Designations follow one of two schemes of GOST 2.201. In the structured one the product is ``.000``, its assemblies
``.100``, ``.200``, theirs ``.110``, and parts take the next numbers of their assembly. In the classifier one each item
gets a classification characteristic by its kind and the next serial number of that characteristic.

Some items come in variants, GOST 2.113: the base one has the designation, the others add «-01», «-02». A part may
come with a second variant in the same assembly, a mirror one or one of another size; an assembly made only of parts
may be a group one, whose variants hold variants of one or two of its parts, and the parent holds some of them. One
group document covers all the variants of an item.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

from synth import vocab


@dataclass
class Part:
    designation: str
    name: str
    kind: vocab.PartKind
    qty: int
    no_drawing: bool = False  # «БЧ»: made without a drawing of its own
    group: str = ""  # the base designation of the group drawing that covers the part; empty for a drawing of its own
    alternatives: list[Part] = field(default_factory=list)  # in a group assembly: the part of each later variant


@dataclass
class Unit:
    """An assembly: the product itself or one of its assembly units."""

    designation: str
    name: str
    qty: int
    parent: Unit | None = None
    units: list[Unit] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    standard: list[tuple[str, int]] = field(default_factory=list)
    other: list[tuple[str, int]] = field(default_factory=list)
    materials: list[tuple[str, str, str]] = field(default_factory=list)  # name, quantity, unit of measure
    variants: list[str] = field(default_factory=list)  # a group assembly: the designations of its variants, base first
    used: list[str] = field(default_factory=list)  # the variants of a group assembly its parent holds
    varied: list[tuple[str, list[int]]] = field(default_factory=list)  # standard items of another quantity by variant

    @property
    def held(self) -> list[str]:
        """What the specification of the parent lists for this assembly, each ``qty`` times."""
        return self.used or [self.designation]

    def walk(self) -> Iterator[Unit]:
        """This assembly and every assembly below it, each before its own."""
        yield self
        for unit in self.units:
            yield from unit.walk()

    @property
    def level(self) -> int:
        return 0 if self.parent is None else self.parent.level + 1


class Numbering:
    """Designations of one kit in one scheme; each is given once."""

    def __init__(self, org: str, scheme: str, rng: random.Random) -> None:
        self.org, self.scheme, self.rng = org, scheme, rng
        self.base = rng.choice(vocab.ASSEMBLY_CLASSES)
        self.serials: defaultdict[str, int] = defaultdict(int)

    def _next(self, code: str) -> str:
        self.serials[code] += 1
        return f"{self.org}.{code}.{self.serials[code]:03d}"

    def _serial(self, designation: str) -> int:
        return int(designation[-3:])

    def root(self) -> str:
        return f"{self.org}.{self.base}.000" if self.scheme == "structured" else self._next(self.base)

    def unit(self, parent: Unit, index: int) -> str:
        """The ``index``-th assembly, from 1, of ``parent``."""
        if self.scheme == "structured":
            step = 100 if parent.parent is None else 10
            return f"{self.org}.{self.base}.{self._serial(parent.designation) + index * step:03d}"
        return self._next(self.rng.choice(vocab.ASSEMBLY_CLASSES))

    def part(self, unit: Unit, index: int, kind: vocab.PartKind) -> str:
        """The ``index``-th part, from 1, of ``unit``."""
        if self.scheme == "structured":
            return f"{self.org}.{self.base}.{self._serial(unit.designation) + index:03d}"
        return self._next(self.rng.choice(vocab.PART_CLASSES[kind.sketch]))

    def stray(self, kind: vocab.PartKind) -> str:
        """A designation that no specification of the kit names: of a part from another product."""
        codes = [c for c in vocab.PART_CLASSES[kind.sketch] if c != self.base]
        return self._next(self.rng.choice(codes))


@dataclass(frozen=True)
class Shape:
    """How big a tree grows: levels of assemblies, and how many children an assembly has."""

    depth: int  # levels of assemblies, the product included: 2 to 4
    units: tuple[int, int] = (1, 3)
    parts: tuple[int, int] = (2, 6)
    standard: tuple[int, int] = (1, 4)
    no_drawing: float = 0.1  # share of parts made without a drawing
    twins: float = 0.06  # share of parts with a second variant in the same assembly
    groups: float = 0.3  # share of assemblies made only of parts that are group ones


class _Names:
    """Names of assemblies, each once in a kit; when the list runs out, numbered ones."""

    def __init__(self, rng: random.Random) -> None:
        self.left = list(vocab.ASSEMBLY_NAMES)
        rng.shuffle(self.left)
        self.extra = 0

    def next(self) -> str:
        if self.left:
            return self.left.pop()
        self.extra += 1
        return f"Узел {self.extra}"


def grow(rng: random.Random, numbering: Numbering, shape: Shape) -> Unit:
    names = _Names(rng)
    root = Unit(numbering.root(), names.next(), 1)
    _fill(root, rng, numbering, shape, names, spine=True)
    for node in list(root.walk()):
        if node.parent is not None and not node.units and rng.random() < shape.groups:
            _group(node, rng)
    root.other = [(rng.choice(vocab.OTHER_ITEMS), 1)] if rng.random() < 0.5 else []
    if rng.random() < 0.6:
        name, unit = rng.choice(vocab.MATERIALS)
        root.materials = [(name, f"{rng.randint(1, 9)},{rng.randint(0, 9)}", unit)]
    return root


def _fill(unit: Unit, rng: random.Random, numbering: Numbering, shape: Shape, names: _Names, *, spine: bool) -> None:
    """Children of ``unit``; the first assemblies down from the product, the spine, reach the full depth."""
    if unit.level < shape.depth - 1:
        low, high = shape.units if unit.parent is None else (0, shape.units[1] - 1)
        for index in range(1, rng.randint(max(low, spine), high) + 1):
            child = Unit(numbering.unit(unit, index), names.next(), rng.randint(1, 2), parent=unit)
            unit.units.append(child)
            _fill(child, rng, numbering, shape, names, spine=spine and index == 1)
    for index in range(1, rng.randint(*shape.parts) + 1):
        kind = rng.choice(vocab.PART_KINDS)
        no_drawing = rng.random() < shape.no_drawing
        part = Part(numbering.part(unit, index, kind), rng.choice(kind.names), kind, rng.randint(1, 4), no_drawing)
        unit.parts.append(part)
        if not no_drawing and rng.random() < shape.twins:
            part.group = part.designation
            unit.parts.append(_variant(part, 1))
    items = rng.sample(vocab.STANDARD_ITEMS, rng.randint(*shape.standard))
    unit.standard = [(item, rng.choice(QUANTITIES)) for item in sorted(items)]


QUANTITIES = (2, 4, 6, 8, 12, 16)  # of a standard item in an assembly


def _variant(part: Part, number: int) -> Part:
    """The variant ``number`` of ``part``, on the group drawing of the part: the same name and kind."""
    return Part(f"{part.designation}-{number:02d}", part.name, part.kind, part.qty, group=part.designation)


def _group(unit: Unit, rng: random.Random) -> None:
    """Make ``unit`` a group assembly: one or two variants more, each with a variant of its own of one or two of the
    parts, and maybe another quantity of a standard item; the parent holds some of the variants."""
    candidates = [p for p in unit.parts if not p.no_drawing and not p.group]
    if not candidates:
        return
    count = 2 if rng.random() < 0.7 else 3
    unit.variants = [unit.designation, *(f"{unit.designation}-{k:02d}" for k in range(1, count))]
    for part in rng.sample(candidates, min(len(candidates), rng.randint(1, 2))):
        part.group = part.designation
        part.alternatives = [_variant(part, k) for k in range(1, count)]
    if unit.standard and rng.random() < 0.5:
        item, qty = unit.standard.pop(rng.randrange(len(unit.standard)))
        others = [q for q in QUANTITIES if q != qty]
        unit.varied = [(item, [qty, *(rng.choice(others) for _ in range(1, count))])]
    unit.used = sorted(rng.sample(unit.variants, rng.randint(1, count)))
