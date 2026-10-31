"""Ground truth of a synthetic kit: what each document holds, which discrepancies and problem files to expect.

The evaluation compares a report with it (``backend/arm_web/evaluate.py`` reads the JSON these models write). Values
are the text drawn on the sheet, and ``boxes`` say where: the page and the box as shares of the page from its top
left corner, as ``kdcheck.schemas.SourceRef`` gives them. On a scan the boxes follow the sheet through its skew and
shift, and ``capture`` says how the pixels were made: by :mod:`synth.degrade` or through Augraphy
(:mod:`synth.augraphy_scans`). The light scans of the demo kit keep the boxes of the sheet and have no ``capture``.

A designation GOST 2.113 shortens to «-01» below the full one is written in full, and ``written`` keeps the text of
the sheet. Fields added after the first truths were written leave the JSON while they hold their default, so the
truths written before them, the committed truth of the demo kit among them, read and write back byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    Tag,
    model_serializer,
)

from synth.model import Change, Drawing, Row, Spec, Variant
from synth.render import Box as DrawnBox


class Truth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _sparse: ClassVar[tuple[str, ...]] = ()  # fields the JSON leaves out while they hold their default

    @model_serializer(mode="wrap")
    def _leave_defaults(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name in self._sparse:
            if getattr(self, name) == type(self).model_fields[name].get_default(call_default_factory=True):
                data.pop(name, None)
        return data


class Box(Truth):
    """Where a value is drawn: the page from 1, and x0, y0, x1, y1 as shares of the page from its top left corner."""

    page: int
    bbox: tuple[float, float, float, float]

    @classmethod
    def of(cls, drawn: DrawnBox | None) -> Box | None:
        return None if drawn is None else cls(page=drawn[0], bbox=drawn[1])


class Stamp(Truth):
    """An ink stamp on the paper copy: its lines, shape and turn, its place on the sheet and its box on the scan."""

    text: list[str]
    shape: Literal["rect", "round"]
    centre: tuple[float, float]  # mm from the top left corner of the sheet
    size: tuple[float, float]  # mm
    angle: float  # degrees, counter clockwise
    colour: tuple[int, int, int]
    box: Box


class Fold(Truth):
    """A crease of a folded sheet: a vertical line for ``axis`` x, ``at`` mm from the left edge or the top."""

    axis: Literal["x", "y"]
    at: float
    shade: int  # grey levels the crease darkens by


class Shadow(Truth):
    """A shadow along the edge of the sheet that lifted off the glass."""

    side: Literal["left", "right", "top", "bottom"]
    width: float  # mm
    depth: int  # grey levels at the edge


class SheetCapture(Truth):
    """What happened to one sheet of a scan: on paper, then on the glass."""

    skew: float = 0.0  # degrees, counter clockwise about the centre
    shift: tuple[float, float] = (0.0, 0.0)  # mm, right and down
    stamps: list[Stamp] = []
    folds: list[Fold] = []
    shadow: Shadow | None = None
    dust: int = 0  # specks on the glass


class Capture(Truth):
    """How a document became pixels (:mod:`synth.degrade`): rasterized from its file or scanned from a paper copy.

    ``quality`` names the class the parameters were drawn from with ``seed``, and the parameters are those applied,
    so a metric can be sliced by class or by any of them. A raster has the resolution, mode and compression only.
    """

    degrader: Literal["synth.degrade"] = "synth.degrade"
    kind: Literal["raster", "scan"]
    quality: Literal["good", "fair", "poor", "unreadable"]
    seed: int
    dpi: int
    mode: Literal["bilevel", "grey", "colour"]
    container: Literal["pdf", "jpg", "png", "tiff"]
    jpeg: int | None = None  # JPEG quality; none for a lossless file
    threshold: int | None = None  # grey level that splits ink from paper in a black and white file
    ink: int = 0  # grey level of the ink on paper
    paper: int = 255  # grey level of clean paper
    spread: float = 0.0  # mm a stroke grows by on each side; it thins below zero
    fade: float = 0.0  # share of the ink lost where it fades most
    stains: int = 0  # grey levels the paper darkens by in its darkest stain
    blur: float = 0.0  # sigma of the optics, mm
    noise: float = 0.0  # sigma of the sensor noise, grey levels
    white: int | None = None  # grey level the scanner turns white and stretches the rest to; none when it does not
    lid: int = 255  # grey level of the scanner lid around a turned or shifted sheet
    sheets: list[SheetCapture] = []

    @property
    def group(self) -> str:
        """The slice of a metric the document falls in: «scan:fair», «raster:good»."""
        return f"{self.kind}:{self.quality}"


class Effect(Truth):
    """An effect of Augraphy that ran on a sheet: its phase, its name, and its settings as the effect held them after
    the run, with the values some effects draw for themselves; images and the data of the sheet are left out."""

    phase: Literal["ink", "paper", "post"]
    name: str
    settings: dict[str, JsonValue] = {}


class AugraphySheet(Truth):
    """What happened to one sheet of a scan through Augraphy, in the order the effects ran."""

    effects: list[Effect] = []
    pixels: tuple[int, int]  # width and height of the scan; a geometric effect may change them
    threshold: int | None = None  # grey level that splits ink from paper in a black and white file, by Otsu


class AugraphyCapture(Truth):
    """How a document became a scan through Augraphy (:mod:`synth.augraphy_scans`).

    The scanner settings are drawn from the class ``quality`` with ``seed`` as :mod:`synth.degrade` draws them, so
    the same document gets the same resolution, pixels and compression from both; what happened on paper and on the
    glass is the effects of the default pipeline of Augraphy ``version``, sheet by sheet.
    """

    degrader: Literal["augraphy"] = "augraphy"
    kind: Literal["scan"] = "scan"
    quality: Literal["good", "fair", "poor"]
    seed: int
    dpi: int
    mode: Literal["bilevel", "grey", "colour"]
    container: Literal["pdf", "jpg", "png", "tiff"]
    jpeg: int | None = None  # JPEG quality; none for a lossless file
    pipeline: Literal["default"] = "default"  # augraphy.default_augraphy_pipeline
    version: str
    sheets: list[AugraphySheet] = []

    @property
    def group(self) -> str:
        """The slice of a metric the document falls in: «augraphy:fair»."""
        return f"augraphy:{self.quality}"


def _degrader(record: Any) -> str:
    """Which degrader wrote a capture record; the records written before Augraphy have no such key."""
    if isinstance(record, dict):
        return str(record.get("degrader", "synth.degrade"))
    return str(getattr(record, "degrader", "synth.degrade"))


CaptureRecord = Annotated[
    Annotated[Capture, Tag("synth.degrade")] | Annotated[AugraphyCapture, Tag("augraphy")],
    Discriminator(_degrader),
]


def _boxes(drawn: dict[str, DrawnBox], prefix: str = "") -> dict[str, Box]:
    """The boxes whose key starts with ``prefix``, keyed by the rest of the key."""
    return {k.removeprefix(prefix): Box(page=v[0], bbox=v[1]) for k, v in sorted(drawn.items()) if k.startswith(prefix)}


class RowTruth(Truth):
    """A specification row as drawn, column by column.

    In a group specification of GOST 2.113 by variant A, ``variant`` names the variant whose variable data hold the
    row; by variant B, ``quantities`` give the quantity on each variant, «×» for a document, and ``qty`` is empty.
    """

    _sparse: ClassVar[tuple[str, ...]] = ("variant", "quantities", "written")

    section: str
    fmt: str = ""
    pos: str = ""
    designation: str = ""
    name: str = ""
    qty: str = ""
    note: str = ""
    boxes: dict[str, Box] = Field(
        {}, description="By column: fmt, pos, designation, name, qty, note; qty:<variant> for each quantity"
    )
    variant: str = ""
    quantities: dict[str, str] = Field({}, description="By the designation of the variant")
    written: str | None = Field(None, description="The designation as the row writes it, «-01»; none when in full")

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
            variant=row.variant,
            quantities=dict(row.quantities),
            written=row.written or None,
        )


class SpecTruth(Truth):
    """A specification; a group one of GOST 2.113 names its variant of layout in ``group`` and its variants."""

    _sparse: ClassVar[tuple[str, ...]] = ("font_type", "group", "variants")

    kind: Literal["specification"] = "specification"
    designation: str
    name: str
    litera: str
    revision: str | None
    rows: list[RowTruth]
    sheets: int = 1
    boxes: dict[str, Box] = Field(
        {},
        description="Fields of the title block and of the left margin columns; variant:<designation> for the heading"
        " of the variable data of each variant",
    )
    origin: Literal["vector", "raster", "scan"] = "vector"
    capture: CaptureRecord | None = None
    font_type: Literal["A", "B"] = Field("A", description="The lettering of GOST 2.304 the document is drawn in")
    group: Literal["A", "B"] | None = Field(None, description="Variant A or B of GOST 2.113; none for a single one")
    variants: list[str] = Field([], description="The variants a group specification covers, the base one first")

    @classmethod
    def of(
        cls, spec: Spec, drawn: dict[str, DrawnBox] | None = None, capture: CaptureRecord | None = None
    ) -> SpecTruth:
        drawn = drawn or {}
        return cls(
            designation=spec.designation,
            name=spec.name,
            litera=spec.litera,
            revision=last_change(spec.changes),
            rows=[RowTruth.of(r, drawn, i) for i, r in enumerate(spec.rows)],
            sheets=max((page for page, _ in drawn.values()), default=1),
            boxes={k: v for k, v in _boxes(drawn).items() if not k.startswith("row:")},
            origin=spec.output.kind,
            capture=capture,
            font_type=spec.font_type,
            group=spec.group or None,
            variants=list(spec.variants),
        )


class ParameterTruth(Truth):
    """A parameter of the technical requirements or the drawing field that extraction should find.

    The value, or the range, and the unit are given where the template of the item writes them: the depth of a layer
    and the hardness of a heat treatment, a roughness, a tolerance of form; none for a parameter written by hand.
    """

    _sparse: ClassVar[tuple[str, ...]] = ("value", "value_min", "value_max", "unit")

    category: str
    name: str
    box: Box | None = None  # where the requirement or the annotation that holds it is drawn
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None


class VariantTruth(Truth):
    """A row of the table of variants of a group drawing, GOST 2.113, 4.1: the variant in full and as the row writes
    it, its mass and its variable dimensions."""

    designation: str
    written: str | None = None
    mass: str | None = None
    data: dict[str, str] = Field({}, description="Variable dimensions by the column of the table: L, B, H")
    boxes: dict[str, Box] = Field({}, description="designation, mass and each column of data")

    @classmethod
    def of(cls, variant: Variant, drawn: dict[str, DrawnBox], row: int) -> VariantTruth:
        return cls(
            designation=variant.designation,
            written=variant.written or None,
            mass=variant.mass or None,
            data=dict(variant.data),
            boxes=_boxes(drawn, f"variant:{row}:"),
        )


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
    """A drawing: the title block and the margin columns as drawn; none for a cell the blank does not have.

    A group drawing of GOST 2.113 lists its variants in ``variants``, and its title block writes «См. табл.» for the
    mass that changes from one variant to another.
    """

    _sparse: ClassVar[tuple[str, ...]] = ("font_type", "variants")

    kind: Literal["assembly_drawing", "part_drawing"]
    origin: Literal["vector", "raster", "scan"]
    designation: str
    name: str
    material: str | None
    mass: str | None
    scale: str | None
    litera: str | None
    revision: str | None
    archive_number: str | None
    first_use: str | None
    requirements: list[str]
    parameters: list[ParameterTruth]
    sheets: int = 1
    boxes: dict[str, Box] = Field({}, description="Fields of the title block, the margin columns, general roughness")
    requirement_boxes: list[Box | None] = []
    capture: CaptureRecord | None = None
    font_type: Literal["A", "B"] = Field("A", description="The lettering of GOST 2.304 the document is drawn in")
    variants: list[VariantTruth] = Field([], description="The table of variants of a group drawing")

    @classmethod
    def of(
        cls, drawing: Drawing, drawn: dict[str, DrawnBox] | None = None, capture: CaptureRecord | None = None
    ) -> DrawingTruth:
        drawn = drawn or {}
        keys = drawing.param_keys or [""] * len(drawing.expected_params)
        return cls(
            kind="assembly_drawing" if drawing.assembly else "part_drawing",
            origin=drawing.output.kind,
            designation=drawing.designation,
            name=drawing.name.replace("\n", " "),
            material=drawing.material or None,
            mass=drawing.mass or None,
            scale=drawing.scale,
            litera=drawing.litera,
            revision=last_change(drawing.changes),
            archive_number=drawing.archive or None,
            first_use=drawing.first_use or None,
            requirements=[r.replace("\n", " ") for r in drawing.requirements],
            parameters=[
                ParameterTruth(
                    category=p.category,
                    name=p.name,
                    box=Box.of(drawn.get(key)),
                    value=p.value,
                    value_min=p.value_min,
                    value_max=p.value_max,
                    unit=p.unit,
                )
                for p, key in zip(drawing.expected_params, keys, strict=True)
            ],
            sheets=drawing.sheets,
            boxes={k: v for k, v in _boxes(drawn).items() if k in TITLE_FIELDS},
            requirement_boxes=[Box.of(drawn.get(f"requirement:{i}")) for i in range(len(drawing.requirements))],
            capture=capture,
            font_type=drawing.font_type,
            variants=[VariantTruth.of(v, drawn, row) for row, v in enumerate(drawing.variants)],
        )


class OtherTruth(Truth):
    """A document of a real kit that is no specification and no drawing: a scheme, an element list, a general view.

    The report calls this kind ``other``; the generators do not make such documents.
    """

    kind: Literal["other"] = "other"
    designation: str
    name: str
    litera: str = ""
    revision: str | None = None
    sheets: int = 1


DocumentTruth = Annotated[SpecTruth | DrawingTruth | OtherTruth, Field(discriminator="kind")]


class ExpectedDiscrepancy(Truth):
    """A discrepancy the check should find: its type in the report and the designation it concerns.

    ``rule_id`` names the Block 1 rule that finds it, none when the core has no such rule yet; ``mutation`` names the
    mutation of :mod:`synth.mutations` that planted it, none for a discrepancy planted by hand. A kit labelled by hand
    gives ``name`` for a specification row without a designation and says in ``comment`` why the discrepancy is there.
    """

    type: str
    designation: str
    rule_id: str | None = None
    mutation: str | None = None
    name: str | None = None
    comment: str | None = None


class KitTruth(Truth):
    root: str
    documents: dict[str, DocumentTruth] = Field(description="By the path of the file in the kit")
    discrepancies: list[ExpectedDiscrepancy] = []
    problem_files: dict[str, str] = Field({}, description="Path of the file → the document status to expect")
    seed: int | None = None
    mutations: list[str] = Field([], description="Mutations applied to the kit, in order")
    notes: list[str] = Field([], description="Disputed cases of a kit labelled by hand and how they were settled")

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> KitTruth:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def last_change(changes: list[Change]) -> str | None:
    """The number of the last change as text; none before the first change."""
    return str(max((c.number for c in changes), default=0) or "") or None
