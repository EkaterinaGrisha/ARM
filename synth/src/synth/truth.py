"""Ground truth of a synthetic kit: what each document holds, which discrepancies and problem files to expect.

The evaluation compares a report with it (``backend/arm_web/evaluate.py`` reads the JSON these models write). Values
are the text drawn on the sheet. Field names and the JSON layout follow the truth of the demo kit, so it stays valid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from synth.model import Change, Drawing, Row, Spec


class Truth(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RowTruth(Truth):
    """A specification row as drawn, column by column."""

    section: str
    fmt: str = ""
    pos: str = ""
    designation: str = ""
    name: str = ""
    qty: str = ""
    note: str = ""

    @classmethod
    def of(cls, row: Row) -> RowTruth:
        return cls(
            section=row.section,
            fmt=row.fmt,
            pos=row.pos,
            designation=row.designation,
            name=row.name,
            qty=row.qty,
            note=row.note,
        )


class SpecTruth(Truth):
    kind: Literal["specification"] = "specification"
    designation: str
    name: str
    litera: str
    revision: str | None
    rows: list[RowTruth]

    @classmethod
    def of(cls, spec: Spec) -> SpecTruth:
        return cls(
            designation=spec.designation,
            name=spec.name,
            litera=spec.litera,
            revision=last_change(spec.changes),
            rows=[RowTruth.of(r) for r in spec.rows],
        )


class ParameterTruth(Truth):
    """A parameter of the technical requirements or the drawing field that extraction should find."""

    category: str
    name: str


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

    @classmethod
    def of(cls, drawing: Drawing) -> DrawingTruth:
        return cls(
            kind="assembly_drawing" if drawing.assembly else "part_drawing",
            origin="vector" if drawing.output == "vector" else "scan",
            designation=drawing.designation,
            name=drawing.name,
            material=drawing.material or None,
            mass=drawing.mass or None,
            scale=drawing.scale,
            litera=drawing.litera,
            revision=last_change(drawing.changes),
            archive_number=drawing.archive or None,
            first_use=drawing.first_use or None,
            requirements=drawing.requirements,
            parameters=[ParameterTruth(category=c, name=n) for c, n in drawing.expected_params],
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
