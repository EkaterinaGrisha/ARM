"""Ground truth of a synthetic kit: what each document holds, which discrepancies and problem files to expect.

The evaluation compares a report with it (``backend/arm_web/evaluate.py`` reads the JSON these models write). Values
are the text drawn on the sheet, and ``boxes`` say where: the page and the box as shares of the page from its top
left corner, as ``kdcheck.schemas.SourceRef`` gives them. A scan keeps the boxes of the sheet it was made from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from synth.model import Change, Drawing, Row, Spec
from synth.render import Box as DrawnBox


class Truth(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Box(Truth):
    """Where a value is drawn: the page from 1, and x0, y0, x1, y1 as shares of the page from its top left corner."""

    page: int
    bbox: tuple[float, float, float, float]

    @classmethod
    def of(cls, drawn: DrawnBox | None) -> Box | None:
        return None if drawn is None else cls(page=drawn[0], bbox=drawn[1])


def _boxes(drawn: dict[str, DrawnBox], prefix: str = "") -> dict[str, Box]:
    """The boxes whose key starts with ``prefix``, keyed by the rest of the key."""
    return {k.removeprefix(prefix): Box(page=v[0], bbox=v[1]) for k, v in sorted(drawn.items()) if k.startswith(prefix)}


class RowTruth(Truth):
    """A specification row as drawn, column by column."""

    section: str
    fmt: str = ""
    pos: str = ""
    designation: str = ""
    name: str = ""
    qty: str = ""
    note: str = ""
    boxes: dict[str, Box] = Field({}, description="By column: fmt, pos, designation, name, qty, note")

    @classmethod
    def of(cls, row: Row, drawn: dict[str, DrawnBox] | None = None, index: int = 0) -> RowTruth:
        return cls(
            section=row.section,
            fmt=row.fmt,
            pos=row.pos,
            designation=row.designation,
            name=row.name.replace("\n", " "),
            qty=row.qty,
            note=row.note,
            boxes=_boxes(drawn or {}, f"row:{index}:"),
        )


class SpecTruth(Truth):
    kind: Literal["specification"] = "specification"
    designation: str
    name: str
    litera: str
    revision: str | None
    rows: list[RowTruth]
    sheets: int = 1
    boxes: dict[str, Box] = Field({}, description="Fields of the title block and of the left margin columns")

    @classmethod
    def of(cls, spec: Spec, drawn: dict[str, DrawnBox] | None = None) -> SpecTruth:
        drawn = drawn or {}
        return cls(
            designation=spec.designation,
            name=spec.name,
            litera=spec.litera,
            revision=last_change(spec.changes),
            rows=[RowTruth.of(r, drawn, i) for i, r in enumerate(spec.rows)],
            sheets=max((page for page, _ in drawn.values()), default=1),
            boxes={k: v for k, v in _boxes(drawn).items() if not k.startswith("row:")},
        )


class ParameterTruth(Truth):
    """A parameter of the technical requirements or the drawing field that extraction should find."""

    category: str
    name: str
    box: Box | None = None  # where the requirement or the annotation that holds it is drawn


TITLE_FIELDS = (
    "designation",
    "name",
    "material",
    "mass",
    "scale",
    "litera",
    "revision",
    "sheet",
    "sheets",
    "archive_number",
    "first_use",
    "general_roughness",
)


class DrawingTruth(Truth):
    kind: Literal["assembly_drawing", "part_drawing"]
    origin: Literal["vector", "scan"]
    designation: str
    name: str
    material: str | None
    mass: str | None
    scale: str
    litera: str
    revision: str | None
    archive_number: str | None
    first_use: str | None
    requirements: list[str]
    parameters: list[ParameterTruth]
    sheets: int = 1
    boxes: dict[str, Box] = Field({}, description="Fields of the title block, the margin columns, general roughness")
    requirement_boxes: list[Box | None] = []

    @classmethod
    def of(cls, drawing: Drawing, drawn: dict[str, DrawnBox] | None = None) -> DrawingTruth:
        drawn = drawn or {}
        keys = drawing.param_keys or [""] * len(drawing.expected_params)
        return cls(
            kind="assembly_drawing" if drawing.assembly else "part_drawing",
            origin="vector" if drawing.output == "vector" else "scan",
            designation=drawing.designation,
            name=drawing.name.replace("\n", " "),
            material=drawing.material or None,
            mass=drawing.mass or None,
            scale=drawing.scale,
            litera=drawing.litera,
            revision=last_change(drawing.changes),
            archive_number=drawing.archive or None,
            first_use=drawing.first_use or None,
            requirements=drawing.requirements,
            parameters=[
                ParameterTruth(category=c, name=n, box=Box.of(drawn.get(key)))
                for (c, n), key in zip(drawing.expected_params, keys, strict=True)
            ],
            sheets=drawing.sheets,
            boxes={k: v for k, v in _boxes(drawn).items() if k in TITLE_FIELDS},
            requirement_boxes=[Box.of(drawn.get(f"requirement:{i}")) for i in range(len(drawing.requirements))],
        )


DocumentTruth = Annotated[SpecTruth | DrawingTruth, Field(discriminator="kind")]


class ExpectedDiscrepancy(Truth):
    """A discrepancy the check should find: its type in the report and the designation it concerns.

    ``rule_id`` names the Block 1 rule that finds it, none when the core has no such rule yet; ``mutation`` names the
    mutation of :mod:`synth.mutations` that planted it, none for a discrepancy planted by hand.
    """

    type: str
    designation: str
    rule_id: str | None = None
    mutation: str | None = None


class KitTruth(Truth):
    root: str
    documents: dict[str, DocumentTruth] = Field(description="By the path of the file in the kit")
    discrepancies: list[ExpectedDiscrepancy] = []
    problem_files: dict[str, str] = Field({}, description="Path of the file → the document status to expect")
    seed: int | None = None
    mutations: list[str] = Field([], description="Mutations applied to the kit, in order")

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> KitTruth:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def last_change(changes: list[Change]) -> str | None:
    """The number of the last change as text; none before the first change."""
    return str(max((c.number for c in changes), default=0) or "") or None
