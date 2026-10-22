"""Documents of a synthetic kit as data: what the renderer draws and the ground truth describes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Change:
    number: int
    notice: str
    date: str


@dataclass
class Drawing:
    file: str
    designation: str
    name: str
    fmt: str = "A3"
    material: str = ""
    mass: str = ""
    scale: str = "1:1"
    litera: str = "О1"
    changes: list[Change] = field(default_factory=list)
    archive: str = ""
    first_use: str = ""
    requirements: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    general_roughness: str = ""
    sketch: str = "disc"
    output: str = "vector"  # vector | scan_pdf | scan_jpg
    assembly: bool = False
    # expected parameters (category, name) for the quality metrics
    expected_params: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Row:
    section: str
    fmt: str = ""
    pos: str = ""
    designation: str = ""
    name: str = ""
    qty: str = ""
    note: str = ""


@dataclass
class Spec:
    file: str
    designation: str
    name: str
    litera: str = "О1"
    changes: list[Change] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    archive: str = ""  # the columns of the left margin
    first_use: str = ""
