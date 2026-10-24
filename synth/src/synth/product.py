"""A random product tree: assemblies with their own assemblies, parts and purchased items, and their designations.

Designations follow one of two schemes of GOST 2.201. In the structured one the product is ``.000``, its assemblies
``.100``, ``.200``, theirs ``.110``, and parts take the next numbers of their assembly. In the classifier one each item
gets a classification characteristic by its kind and the next serial number of that characteristic.
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
    items = rng.sample(vocab.STANDARD_ITEMS, rng.randint(*shape.standard))
    unit.standard = [(item, rng.choice((2, 4, 6, 8, 12, 16))) for item in sorted(items)]
