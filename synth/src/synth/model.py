"""Documents of a synthetic kit as data: what the renderer draws and the ground truth describes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["vector", "raster", "scan"]
Container = Literal["pdf", "jpg", "png", "tiff"]
Quality = Literal["", "good", "fair", "poor", "unreadable"]


@dataclass
class Change:
    number: int
    notice: str
    date: str


@dataclass
class Output:
    """How a document goes into the kit: a vector PDF, or pixels rasterized from the file or scanned from paper.

    ``quality`` is the class of :mod:`synth.degrade` the pixels come from; empty for the light scans of the demo kit.
    """

    kind: Kind = "vector"
    container: Container = "pdf"
    quality: Quality = ""


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
    output: Output = field(default_factory=Output)
    assembly: bool = False
    # expected parameters (category, name) for the quality metrics
    expected_params: list[tuple[str, str]] = field(default_factory=list)
    # where each expected parameter is drawn, as the renderer keys it: «requirement:0», «annotation:2»,
    # «sheet2:annotation:0», «general_roughness»; none for parameters written by hand
    param_keys: list[str] = field(default_factory=list)
    more_sheets: list[list[str]] = field(default_factory=list)  # annotations of the sheets after the first
    missing_sheets: int = 0  # last sheets left out of the file, though the title block counts them

    @property
    def sheets(self) -> int:
        """Sheets of the document, as its title block says."""
        return 1 + len(self.more_sheets)


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
    output: Output = field(default_factory=Output)
