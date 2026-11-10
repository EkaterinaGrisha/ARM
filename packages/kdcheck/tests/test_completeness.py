"""Block 1 on synthetic structures: no PDFs, only the tree and the check logic.

Every rule of the registry has a case where it fires and a near miss where it stays quiet (``CASES``).
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, get_args

import pytest
from kdcheck.checks.completeness import check as run_check
from kdcheck.checks.rules import RULES, configured
from kdcheck.config import Checks, ConfigError, RuleSettings, load_settings
from kdcheck.normalize.designations import Designations
from kdcheck.report.labels import SEVERITY
from kdcheck.schemas import (
    Discrepancy,
    DiscrepancyType,
    DocumentInfo,
    Extracted,
    PageInfo,
    Parameter,
    PartRecord,
    SourceRef,
    Specification,
    SpecRow,
    TitleBlock,
    TreeNode,
)
from kdcheck.tree.documents import assemble
from kdcheck.tree.product import DrawingRef, build_tree

SETTINGS = load_settings()
ESKD = Designations(SETTINGS.dictionaries)


class Result(NamedTuple):
    tree: list[TreeNode]
    discrepancies: list[Discrepancy]
    log: list[str]


def check(
    specs,
    drawings,
    checks: Checks = SETTINGS.checks,
    later: set[str] | None = None,
    unread: list[DocumentInfo] | None = None,
) -> Result:
    """The tree and the discrepancies as the pipeline gets them: files assembled into documents first. ``later`` are
    the files with form 2a of a later sheet; by default every file past sheet 1. ``unread`` are files without a
    designation read."""
    parts = [d.part for d in drawings if d.part is not None]
    infos = [info for info, _ in specs] + [d.doc for d in drawings]
    if later is None:
        later = {info.id for info in infos if (info.sheet or 0) > 1}
    assembly = assemble(infos, specs, parts, later)
    refs = [d for d in drawings if d.doc.sheet_of is None]
    tree = build_tree(assembly.specs, refs, ESKD, assembly.documents, unread)
    found = run_check(tree, ESKD, configured(checks), checks)
    return Result(tree.roots, found, assembly.log + tree.log)


def row(i: int, section: str, des: str = "", name: str = "", qty: float | None = 1, note: str = "") -> SpecRow:
    return SpecRow(
        id=f"r{i}",
        section=section,
        designation=Extracted(value=des) if des else None,
        name=Extracted(value=name),
        quantity=Extracted(value=qty) if qty is not None else None,
        note=Extracted(value=note) if note else None,
    )


def spec(doc_id: str, des: str, name: str, rows: list[SpecRow], litera: str = "О1"):
    info = DocumentInfo(
        id=doc_id, file_name=f"{doc_id}.pdf", path=f"{doc_id}.pdf", kind="specification", designation=des
    )
    return info, Specification(
        document_id=doc_id,
        designation=Extracted(value=des),
        name=Extracted(value=name),
        litera=Extracted(value=litera),
        rows=rows,
    )


def drawing(
    doc_id: str,
    des: str,
    name: str,
    litera: str = "О1",
    revision: int | None = None,
    sheet: int | None = None,
    sheets: int | None = None,
):
    """A drawing of one file; ``sheet`` is the sheet the file starts with, as the pipeline sets it."""
    info = DocumentInfo(
        id=doc_id,
        file_name=f"{doc_id}.pdf",
        path=f"{doc_id}.pdf",
        kind="part_drawing",
        designation=des,
        name=name,
        sheet=sheet,
    )
    tb = TitleBlock(
        designation=Extracted(value=des),
        name=Extracted(value=name),
        litera=Extracted(value=litera),
        revision=Extracted(value=revision) if revision else None,
        sheet=Extracted(value=sheet) if sheet else None,
        sheets=Extracted(value=sheets, source=SourceRef(document_id=doc_id, method="text_layer")) if sheets else None,
    )
    return DrawingRef(doc=info, part=PartRecord(document_id=doc_id, designation=des, name=name, title_block=tb))


def found(result) -> set[tuple[str | None, str | None]]:
    return {(d.rule_id, d.designation) for d in result.discrepancies}


def test_tree_recursion_and_quantities():
    specs = [
        spec(
            "s0",
            "АБВГ.100000.000",
            "Изделие",
            [
                row(1, "Документация", "АБВГ.100000.000 СБ", "Сборочный чертёж", None),
                row(2, "Сборочные единицы", "АБВГ.100000.100", "Узел", 2),
                row(3, "Стандартные изделия", "", "Болт М8 ГОСТ 7798-70", 4),
            ],
        ),
        spec(
            "s1",
            "АБВГ.100000.100",
            "Узел",
            [
                row(1, "Документация", "АБВГ.100000.100 СБ", "Сборочный чертёж", None),
                row(2, "Детали", "АБВГ.100000.101", "Ось", 3),
            ],
        ),
    ]
    drawings = [
        drawing("d0", "АБВГ.100000.000 СБ", "Изделие"),
        drawing("d1", "АБВГ.100000.100 СБ", "Узел"),
        drawing("d2", "АБВГ.100000.101", "Ось"),
    ]
    result = check(specs, drawings)
    assert result.discrepancies == []
    root = result.tree[0]
    assert root.designation == "АБВГ.100000.000"
    unit = next(n for n in root.children if n.designation == "АБВГ.100000.100")
    axle = next(n for n in unit.children if n.designation == "АБВГ.100000.101")
    assert axle.total_quantity == 6  # 2 units × 3 axles
    assert axle.drawing_document_ids == ["d2"]
    bolt = next(n for n in root.children if n.kind == "standard")
    assert bolt.status == "not_required"


def group_row(i: int, section: str, des: str, name: str, quantities: dict[str, float | str]) -> SpecRow:
    """A row of a group specification by variant B of GOST 2.113: a quantity for each variant, «×» for a document."""
    found = {number: Extracted(value=value) for number, value in quantities.items()}
    return SpecRow(
        id=f"r{i}", section=section, designation=Extracted(value=des), name=Extracted(value=name), quantities=found
    )


def test_a_variant_is_expanded_through_the_group_specification_of_its_base():
    """The product lists the base unit and its variant 01; one group specification covers both (ADR-0025). Each gets
    the rows with a quantity in its column, with that quantity; a row with no quantity read goes to both."""
    info, group = spec(
        "s1",
        "АБВГ.100000.100",
        "Узел",
        [
            group_row(1, "Документация", "АБВГ.100000.100 СБ", "Сборочный чертёж", {"–": "×", "01": "×"}),
            group_row(2, "Детали", "АБВГ.100000.101", "Ось", {"–": 2, "01": 1}),
            group_row(3, "Детали", "АБВГ.100000.102", "Втулка", {"01": 4}),
            group_row(4, "Детали", "АБВГ.100000.103", "Шайба", {}),
        ],
    )
    group.variants = ["–", "01"]
    product = spec(
        "s0",
        "АБВГ.100000.000",
        "Изделие",
        [
            row(1, "Сборочные единицы", "АБВГ.100000.100", "Узел", 2),
            row(2, "Сборочные единицы", "АБВГ.100000.100-01", "Узел", 1),
        ],
    )
    drawings = [
        drawing("d1", "АБВГ.100000.100 СБ", "Узел"),
        drawing("d2", "АБВГ.100000.101", "Ось"),
        drawing("d3", "АБВГ.100000.102", "Втулка"),
        drawing("d4", "АБВГ.100000.103", "Шайба"),
    ]
    result = check([product, (info, group)], drawings)
    [root] = result.tree
    assert root.designation == "АБВГ.100000.000"
    base, variant = root.children
    assert (base.spec_document_id, variant.spec_document_id) == ("s1", "s1")
    assert variant.drawing_document_ids == ["d1"]  # the group assembly drawing
    by_child = {node.designation: [(c.designation, c.quantity) for c in node.children[1:]] for node in (base, variant)}
    assert by_child == {
        "АБВГ.100000.100": [("АБВГ.100000.101", 2.0), ("АБВГ.100000.103", None)],
        "АБВГ.100000.100-01": [("АБВГ.100000.101", 1.0), ("АБВГ.100000.102", 4.0), ("АБВГ.100000.103", None)],
    }
    assert variant.children[2].total_quantity == 4.0
    assert found(result) == set()


def test_a_group_specification_named_only_by_a_variant_is_not_a_second_product():
    info, group = spec("s1", "АБВГ.100000.100", "Узел", [group_row(1, "Детали", "АБВГ.100000.101", "Ось", {"01": 1})])
    group.variants = ["–", "01"]
    product = spec("s0", "АБВГ.100000.000", "Изделие", [row(1, "Сборочные единицы", "АБВГ.100000.100-01", "Узел")])
    result = check([product, (info, group)], [drawing("d2", "АБВГ.100000.101", "Ось")])
    [root] = result.tree
    assert root.designation == "АБВГ.100000.000" and root.children[0].children[0].designation == "АБВГ.100000.101"


def test_a_row_under_two_variants_is_one_missing_drawing():
    info, group = spec(
        "s1", "АБВГ.100000.100", "Узел", [group_row(1, "Детали", "АБВГ.100000.101", "Ось", {"–": 1, "01": 1})]
    )
    group.variants = ["–", "01"]
    product = spec(
        "s0",
        "АБВГ.100000.000",
        "Изделие",
        [
            row(1, "Сборочные единицы", "АБВГ.100000.100", "Узел"),
            row(2, "Сборочные единицы", "АБВГ.100000.100-01", "Узел"),
        ],
    )
    result = check([product, (info, group)], [])
    [missing] = [d for d in result.discrepancies if d.rule_id == "B1.MISSING.001"]
    assert missing.designation == "АБВГ.100000.101" and len(missing.tree_node_ids) == 2


def variable_row(i: int, section: str, des: str, name: str, variant: str, qty: float = 1) -> SpecRow:
    """A row of the variable data of a group specification by variant A of GOST 2.113, under its variant."""
    found = row(i, section, des, name, qty)
    found.variant = variant
    return found


def test_a_variant_takes_the_constant_data_and_its_own_by_variant_a():
    """By variant A the constant rows go to every variant and the variable ones to theirs (ADR-0026). The document of
    a variant no specification names is still listed, so it is in a specification."""
    info, group = spec(
        "s1",
        "АБВГ.100000.100",
        "Узел",
        [
            row(1, "Детали", "АБВГ.100000.101", "Ось", 2),
            variable_row(2, "Детали", "АБВГ.100000.102", "Втулка", "–"),
            variable_row(3, "Детали", "АБВГ.100000.103", "Втулка", "01", 3),
            variable_row(4, "Детали", "АБВГ.100000.104", "Кольцо", "02"),
        ],
    )
    group.variants = ["–", "01", "02"]
    product = spec(
        "s0",
        "АБВГ.100000.000",
        "Изделие",
        [
            row(1, "Сборочные единицы", "АБВГ.100000.100", "Узел"),
            row(2, "Сборочные единицы", "АБВГ.100000.100-01", "Узел"),
        ],
    )
    drawings = [
        drawing(f"d{n}", f"АБВГ.100000.10{n}", name) for n, name in ((1, "Ось"), (2, "Втулка"), (3, "Втулка"))
    ] + [drawing("d4", "АБВГ.100000.104", "Кольцо")]
    result = check([product, (info, group)], drawings)
    base, variant = result.tree[0].children
    assert [(c.designation, c.quantity) for c in base.children] == [("АБВГ.100000.101", 2), ("АБВГ.100000.102", 1)]
    assert [(c.designation, c.quantity) for c in variant.children] == [("АБВГ.100000.101", 2), ("АБВГ.100000.103", 3)]
    assert found(result) == set()  # variant 02 is named by no one, and its ring is listed all the same


@pytest.mark.parametrize(("fmt", "note", "missing"), [("БЧ", "", False), ("Б Ч", "", False), ("А4", "", True)])
def test_a_part_without_a_drawing_is_marked_in_the_format_column(fmt, note, missing):
    """GOST 2.106 writes «БЧ» in the format column of a part made without a drawing; a format names its drawing."""
    part = row(1, "Детали", PART, "Вал", note=note)
    part.format = Extracted(value=fmt)
    result = check([product(part)], [])
    assert (("B1.MISSING.001", PART) in found(result)) is missing


@pytest.mark.parametrize(("mass", "missing"), [("См. табл.", False), ("1.2", True)])
def test_a_variant_of_a_part_is_drawn_on_the_group_drawing_of_its_base(mass, missing):
    """A row «-01» below the base part names its variant: the group drawing of the base covers it, a drawing that is
    not a group one does not (GOST 2.113, 4.1.10)."""
    product = spec(
        "s0",
        "АБВГ.100000.000",
        "Изделие",
        [row(1, "Детали", "АБВГ.100000.001", "Ось"), row(2, "Детали", "АБВГ.100000.001-01", "Ось")],
    )
    base = drawing("d1", "АБВГ.100000.001", "Ось")
    base.part.title_block.mass = Extracted(value=mass)
    result = check([product], [base])
    assert (("B1.MISSING.001", "АБВГ.100000.001-01") in found(result)) is missing
    assert result.tree[0].children[1].drawing_document_ids == ([] if missing else ["d1"])


def test_all_discrepancy_types():
    specs = [
        spec(
            "s0",
            "АБВГ.100000.000",
            "Изделие",
            [
                row(1, "Сборочные единицы", "АБВГ.100000.100", "Узел"),
                row(2, "Сборочные единицы", "АБВГ.100000.200", "Узел без спецификации"),
                row(3, "Детали", "АБВГ.100000.001", "Вал"),
                row(4, "Детали", "АБВГ.100000.002", "Втулка"),
                row(5, "Детали", "АБВГ.100000.003", "Шайба", note="БЧ"),  # no drawing needed (БЧ)
            ],
        ),
        spec("s1", "АБВГ.100000.100", "Узел", [row(1, "Детали", "АБВГ.100000.101", "Ось")]),
    ]
    drawings = [
        drawing("d1", "АБВГ.100000.001", "Вал ступенчатый"),  # name differs
        drawing("d2", "АБВГ.100000.101", "Ось", litera="О"),  # litera below the assembly litera
        drawing("d3", "АБВГ.999999.001", "Чужая деталь"),  # not in any specification
        drawing("d4", "АБВГ.100000.002", "Втулка", revision=1),
        drawing("d5", "АБВГ.100000.002", "Втулка", revision=2),  # duplicate with another change number
    ]
    result = check(specs, drawings)
    assert found(result) == {
        ("B1.SPEC.001", "АБВГ.100000.200"),
        ("B1.NAME.001", "АБВГ.100000.001"),
        ("B1.LITERA.002", "АБВГ.100000.101"),
        ("B1.ORPHAN.001", "АБВГ.999999.001"),
        ("B1.DUP.001", "АБВГ.100000.002"),
        ("B1.VERSION.001", "АБВГ.100000.002"),
    }
    assert {d.type for d in result.discrepancies} == {
        "missing_specification",
        "name_mismatch",
        "revision_mismatch",
        "orphan_drawing",
        "duplicate_designation",
    }


def test_missing_drawing_and_multisheet_is_not_duplicate():
    specs = [
        spec(
            "s0",
            "АБВГ.100000.000",
            "Изделие",
            [
                row(1, "Детали", "АБВГ.100000.001", "Вал"),
                row(2, "Детали", "АБВГ.100000.002", "Корпус"),
            ],
        )
    ]
    drawings = [
        drawing("d1", "АБВГ.100000.002", "Корпус", sheet=1),
        drawing("d2", "АБВГ.100000.002", "Корпус", sheet=2),
    ]
    result = check(specs, drawings)
    assert found(result) == {("B1.MISSING.001", "АБВГ.100000.001")}
    missing = next(n for n in result.tree[0].children if n.designation == "АБВГ.100000.001")
    assert missing.status == "missing" and missing.discrepancy_ids


def test_no_specifications_is_reported_not_crashed():
    result = check([], [drawing("d1", "АБВГ.100000.001", "Вал")])
    assert result.tree == []
    assert result.discrepancies == []
    assert result.log


# ---------------------------------------------------------------- one case and one near miss per rule

TOP, UNIT, PART = "АБВГ.100000.000", "АБВГ.100000.100", "АБВГ.100000.001"
AXLE = "АБВГ.100000.101"
Kit = tuple[list[tuple[DocumentInfo, Specification]], list[DrawingRef]]


def product(*rows: SpecRow, litera: str = "О1") -> tuple[DocumentInfo, Specification]:
    return spec("s0", TOP, "Изделие", list(rows), litera=litera)


def part_row(name: str = "Вал", note: str = "") -> SpecRow:
    return row(1, "Детали", PART, name, note=note)


def unit_row() -> SpecRow:
    return row(2, "Сборочные единицы", UNIT, "Узел")


def drawing_row() -> SpecRow:
    return row(3, "Документация", f"{TOP} СБ", "Сборочный чертёж", None)


def sized(ref: DrawingRef, *formats: str) -> DrawingRef:
    """The drawing on sheets of these formats of GOST 2.301, as ingest names them."""
    ref.doc.pages = [PageInfo(number=n, width=595, height=842, format=f) for n, f in enumerate(formats, start=1)]
    return ref


def formatted(found: SpecRow, fmt: str) -> SpecRow:
    found.format = Extracted(value=fmt)
    return found


def read_row(found: SpecRow, position: str | None, method: str = "text_layer") -> SpecRow:
    """The row as a reader gives it: its position, and its source read in this way."""
    found.position = Extracted(value=position) if position else None
    found.source = SourceRef(document_id="s0", method=method)
    return found


def first_used(ref: DrawingRef, where: str) -> DrawingRef:
    ref.part.title_block.first_use = Extracted(value=where)
    return ref


CASES: dict[str, tuple[Callable[[], Kit], Callable[[], Kit]]] = {
    "B1.MISSING.001": (
        lambda: ([product(part_row())], []),
        lambda: ([product(part_row(note="БЧ"))], []),  # a part without a drawing by design
    ),
    "B1.MISSING.002": (
        lambda: ([product(drawing_row())], []),
        lambda: ([product(drawing_row())], [drawing("d0", f"{TOP} СБ", "Изделие")]),
    ),
    "B1.SPEC.001": (
        lambda: ([product(unit_row())], []),
        lambda: ([product(unit_row()), spec("s1", UNIT, "Узел", [])], []),
    ),
    "B1.ORPHAN.001": (
        lambda: ([product(part_row())], [drawing("d1", PART, "Вал"), drawing("d9", "АБВГ.999999.001", "Чужая")]),
        # the assembly drawing of a listed unit belongs to the kit even when no specification names it
        lambda: ([product(unit_row()), spec("s1", UNIT, "Узел", [])], [drawing("d2", f"{UNIT} СБ", "Узел")]),
    ),
    "B1.NAME.001": (
        lambda: ([product(part_row())], [drawing("d1", PART, "Вал ступенчатый")]),
        lambda: ([product(part_row())], [drawing("d1", PART, "ВАЛ")]),  # case is not a discrepancy
    ),
    "B1.NAME.002": (
        lambda: ([product(unit_row()), spec("s1", UNIT, "Узел в сборе", [])], []),
        lambda: ([product(unit_row()), spec("s1", UNIT, "УЗЕЛ", [])], []),
    ),
    "B1.DUP.001": (
        lambda: ([product(part_row())], [drawing("d1", PART, "Вал"), drawing("d2", PART, "Вал")]),
        # two sheets of one document in two files
        lambda: ([product(part_row())], [drawing("d1", PART, "Вал", sheet=1), drawing("d2", PART, "Вал", sheet=2)]),
    ),
    "B1.DUP.002": (
        lambda: ([product(part_row(), unit_row()), spec("s1", UNIT, "Узел", [part_row("Ось")])], []),
        lambda: ([product(part_row(), unit_row()), spec("s1", UNIT, "Узел", [part_row("ВАЛ")])], []),
    ),
    "B1.DUP.003": (
        lambda: ([product(unit_row()), spec("s1", UNIT, "Узел", []), spec("s2", UNIT, "Узел", [])], []),
        # a specification and the assembly drawing of the same unit
        lambda: ([product(unit_row()), spec("s1", UNIT, "Узел", [])], [drawing("d2", f"{UNIT} СБ", "Узел")]),
    ),
    "B1.VERSION.001": (
        lambda: (
            [product(part_row())],
            [drawing("d1", PART, "Вал", revision=1), drawing("d2", PART, "Вал", revision=2)],
        ),
        lambda: (  # copies of one version: a duplicate, not a version mismatch
            [product(part_row())],
            [drawing("d1", PART, "Вал", revision=1), drawing("d2", PART, "Вал", revision=1)],
        ),
    ),
    "B1.LITERA.001": (
        lambda: ([product(drawing_row())], [drawing("d0", f"{TOP} СБ", "Изделие", litera="О")]),
        lambda: ([product(drawing_row())], [drawing("d0", f"{TOP} СБ", "Изделие", litera="О1")]),
    ),
    "B1.LITERA.002": (
        lambda: ([product(part_row())], [drawing("d1", PART, "Вал", litera="О")]),
        lambda: ([product(part_row())], [drawing("d1", PART, "Вал", litera="О2")]),  # a later stage is fine
    ),
    "B1.SHEETS.001": (  # the title block counts three sheets, two are in the kit
        lambda: (
            [product(part_row())],
            [drawing("d1", PART, "Вал", sheet=1, sheets=3), drawing("d2", PART, "Вал", sheet=2)],
        ),
        lambda: (
            [product(part_row())],
            [drawing("d1", PART, "Вал", sheet=1, sheets=2), drawing("d2", PART, "Вал", sheet=2)],
        ),
    ),
    "B1.FORMAT.001": (
        lambda: ([product(formatted(part_row(), "А3"))], [sized(drawing("d1", PART, "Вал"), "A4")]),
        lambda: ([product(formatted(part_row(), "A4"))], [sized(drawing("d1", PART, "Вал"), "A4")]),  # Latin «A»
    ),
    "B1.SECTION.001": (  # an assembly with a specification of its own written in «Детали»
        lambda: ([product(row(1, "Детали", UNIT, "Узел")), spec("s1", UNIT, "Узел", [])], []),
        lambda: ([product(unit_row()), spec("s1", UNIT, "Узел", [])], []),
    ),
    "B1.POS.001": (
        lambda: ([product(read_row(part_row(), "1"), read_row(row(2, "Детали", f"{PART[:-1]}2", "Ось"), "1"))], []),
        lambda: ([product(read_row(part_row(), "1"), read_row(row(2, "Детали", f"{PART[:-1]}2", "Ось"), "2"))], []),
    ),
    "B1.POS.002": (
        lambda: ([product(read_row(row(1, "Детали", PART, "Вал", None), "1"))], []),
        lambda: ([product(read_row(row(3, "Документация", f"{TOP} СБ", "Сборочный чертёж", None), None))], []),
    ),
    "B1.POS.003": (  # a part with no designation, read from a text layer; a standard item and a document have none
        lambda: ([product(read_row(row(1, "Детали", "", "Вал"), "1"))], []),
        lambda: (
            [
                product(
                    read_row(row(1, "Стандартные изделия", "", "Болт М8 ГОСТ 7798-70", 4), "1"),
                    read_row(row(2, "Документация", "", "Схема электрическая принципиальная", None), None),
                )
            ],
            [],
        ),
    ),
    "B1.FIRSTUSE.001": (  # the shaft names the unit as its first use, and the unit lists only an axle
        lambda: (
            [product(part_row(), unit_row()), spec("s1", UNIT, "Узел", [read_row(row(1, "Детали", AXLE, "Ось"), "1")])],
            [first_used(drawing("d1", PART, "Вал"), UNIT)],
        ),
        # first used in another product, outside the kit
        lambda: ([product(part_row())], [first_used(drawing("d1", PART, "Вал"), "АБВГ.999999.000")]),
    ),
}


def fired(kit: Kit, checks: Checks = SETTINGS.checks) -> set[str | None]:
    return {d.rule_id for d in check(*kit, checks=checks).discrepancies}


def test_every_rule_has_a_case_and_a_near_miss():
    assert list(CASES) == [rule.id for rule in RULES]


@pytest.mark.parametrize("rule_id", list(CASES))
def test_rule_fires(rule_id):
    assert rule_id in fired(CASES[rule_id][0]())


@pytest.mark.parametrize("rule_id", list(CASES))
def test_rule_stays_quiet_on_a_near_miss(rule_id):
    assert rule_id not in fired(CASES[rule_id][1]())


def _read_by(extracted: Extracted | None, method: str) -> None:
    assert extracted is not None
    extracted.source = SourceRef(document_id="d", method=method)


def _kit_with_name(on_drawing: str, method: str) -> Kit:
    ref = drawing("d1", PART, on_drawing)
    _read_by(ref.part.title_block.name, method)
    return [product(part_row("Шайба специальная"))], [ref]


def test_a_misread_letter_of_an_ocr_name_is_not_a_mismatch():
    assert "B1.NAME.001" not in fired(_kit_with_name("Шацба специольная", "ocr"))


def test_another_name_read_by_ocr_is_a_mismatch():
    assert "B1.NAME.001" in fired(_kit_with_name("Планка", "ocr"))


def test_the_text_layer_gets_no_margin():
    assert "B1.NAME.001" in fired(_kit_with_name("Шацба специольная", "text_layer"))


def test_positions_with_two_readings_of_one_name_are_not_a_duplicate():
    one, other = part_row("Шайба специальная"), part_row("Шацба специольная")
    _read_by(other.name, "ocr")
    kit = [product(one, unit_row()), spec("s1", UNIT, "Узел", [other])], []
    assert "B1.DUP.002" not in fired(kit)


def test_a_part_written_in_the_assemblies_is_a_part_of_the_wrong_section():
    """A part drawing and neither a specification nor an assembly drawing: the row is a part, so its drawing is found
    and no specification is missing."""
    result = check([product(row(2, "Сборочные единицы", PART, "Вал"))], [drawing("d1", PART, "Вал")])
    assert found(result) == {("B1.SECTION.001", PART)}
    [shaft] = result.tree[0].children
    assert (shaft.kind, shaft.section, shaft.drawing_document_ids) == ("part", "Сборочные единицы", ["d1"])


def test_an_assembly_written_in_the_parts_is_expanded_through_its_specification():
    kit = [product(row(1, "Детали", UNIT, "Узел")), spec("s1", UNIT, "Узел", [row(1, "Детали", PART, "Вал")])]
    result = check(kit, [drawing("d1", PART, "Вал"), drawing("d2", f"{UNIT} СБ", "Узел")])
    assert found(result) == {("B1.SECTION.001", UNIT)}
    [unit] = result.tree[0].children
    assert (unit.kind, unit.children[0].designation) == ("assembly", PART)


def test_the_format_of_an_assembly_is_that_of_its_specification_or_its_assembly_drawing():
    """GOST 2.106: the format column names the format of the document the row designates, the specification of an
    assembly; some makers write that of its assembly drawing. A document on sheets of several formats is written «*»
    and is not checked."""

    def kit(fmt: str, sb: str | None) -> Kit:
        unit = spec("s1", UNIT, "Узел", [])
        unit[0].pages = [PageInfo(number=1, width=595, height=842, format="A4")]
        drawings = [sized(drawing("d2", f"{UNIT} СБ", "Узел"), sb)] if sb else []
        return [product(formatted(unit_row(), fmt)), unit], drawings

    assert "B1.FORMAT.001" not in fired(kit("А4", "A3"))
    assert "B1.FORMAT.001" not in fired(kit("А3", "A3"))
    assert "B1.FORMAT.001" in fired(kit("А3", None))
    assert "B1.FORMAT.001" in fired(kit("А2", "A3"))
    starred = [product(formatted(part_row(), "А4*"))], [sized(drawing("d1", PART, "Вал"), "A4", "A3")]
    assert "B1.FORMAT.001" not in fired(starred)


def test_first_use_in_a_specification_without_rows_read_or_read_by_ocr_is_not_reported():
    """A specification whose rows were not read tells nothing, nor does one read by OCR, which may miss a row."""
    empty = spec("s1", UNIT, "Узел", [])
    shaft = first_used(drawing("d1", PART, "Вал"), UNIT)
    assert "B1.FIRSTUSE.001" not in fired(([product(part_row(), unit_row()), empty], [shaft]))
    scanned = spec("s1", UNIT, "Узел", [read_row(row(1, "Детали", AXLE, "Ось"), "1", "ocr")])
    assert "B1.FIRSTUSE.001" not in fired(([product(part_row(), unit_row()), scanned], [shaft]))


def test_the_assembly_drawing_names_the_product_its_assembly_goes_into_as_first_use():
    """The specification and the assembly drawing of an assembly name the product it goes into; a drawing that names
    its own assembly, which lists it, is taken too."""
    unit = spec("s1", UNIT, "Узел", [row(1, "Документация", f"{UNIT} СБ", "Сборочный чертёж", None)])
    for where in (TOP, UNIT):
        sb = first_used(drawing("d2", f"{UNIT} СБ", "Узел"), where)
        assert "B1.FIRSTUSE.001" not in fired(([product(unit_row()), unit], [sb]))


def test_positions_read_by_ocr_and_variants_sharing_one_are_not_anomalies():
    shaft, axle = read_row(part_row(), "1", "ocr"), read_row(row(2, "Детали", f"{PART[:-1]}2", "Ось"), "1", "ocr")
    assert "B1.POS.001" not in fired(([product(shaft, axle)], []))
    base, other = (
        read_row(variable_row(i, "Детали", f"{PART[:-1]}{i}", "Ось", v), "3") for i, v in ((2, "–"), (3, "01"))
    )
    assert "B1.POS.001" not in fired(([product(read_row(part_row(), "1"), base, other)], []))
    by_b = [read_row(group_row(i, "Детали", f"{PART[:-1]}{i}", "Ось", {v: 1}), "3") for i, v in ((2, "–"), (3, "01"))]
    assert "B1.POS.001" not in fired(([product(*by_b)], []))
    assert "B1.POS.001" in fired(
        ([product(*by_b, read_row(group_row(4, "Детали", f"{PART[:-1]}4", "Ось", {"01": 2}), "3"))], [])
    )


def test_a_gap_in_the_positions_is_no_anomaly():
    """GOST 2.106 lets a specification reserve position numbers."""
    rows = [read_row(part_row(), "1"), read_row(row(2, "Детали", f"{PART[:-1]}2", "Ось"), "5")]
    assert not {"B1.POS.001", "B1.POS.002"} & fired(([product(*rows)], []))


def test_an_assembly_drawing_without_a_specification_in_a_kit_without_any():
    """A kit without specifications: GOST 2.102 makes the specification the main document of an assembly."""
    result = check([], [drawing("d2", f"{UNIT} СБ", "Узел"), drawing("d1", PART, "Вал")])
    assert found(result) == {("B1.SPEC.001", UNIT)}


def test_a_missing_drawing_is_preliminary_while_a_file_is_not_read():
    scan = DocumentInfo(id="u1", file_name="скан.jpg", path="скан.jpg", status="unreadable")
    [missing] = check([product(part_row())], [], unread=[scan]).discrepancies
    assert (missing.rule_id, missing.preliminary) == ("B1.MISSING.001", True)
    assert "u1" in missing.document_ids and "скан.jpg" in missing.message
    [sure] = check([product(part_row())], []).discrepancies
    assert not sure.preliminary


def test_a_drawing_in_no_specification_is_preliminary_while_one_has_no_rows_read():
    kit = [product(part_row()), spec("s9", "АБВГ.200000.000", "Другое изделие", [])]
    drawings = [drawing("d1", PART, "Вал"), drawing("d9", "АБВГ.200000.001", "Ось")]
    [orphan] = [d for d in check(kit, drawings).discrepancies if d.rule_id == "B1.ORPHAN.001"]
    assert orphan.preliminary and "s9" in orphan.document_ids


def test_rule_ids_are_unique_and_cover_every_discrepancy_type():
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"B1\.[A-Z]+\.\d{3}", rule_id) for rule_id in ids)
    assert {rule.type for rule in RULES} == set(get_args(DiscrepancyType))


def test_settings_turn_a_rule_off_and_set_its_severity():
    kit = CASES["B1.NAME.001"][0]()
    rules = SETTINGS.checks.rules
    off = SETTINGS.checks.model_copy(
        update={"rules": {**rules, "B1.NAME.001": RuleSettings(enabled=False, severity="medium")}}
    )
    assert "B1.NAME.001" not in fired(kit, off)
    low = SETTINGS.checks.model_copy(
        update={"rules": {**rules, "B1.NAME.001": RuleSettings(enabled=True, severity="low")}}
    )
    [name] = [d for d in check(*kit, checks=low).discrepancies if d.rule_id == "B1.NAME.001"]
    assert (name.type, name.severity, name.title) == (
        "name_mismatch",
        "low",
        "Наименование на чертеже не совпадает со спецификацией",
    )


def test_checks_yaml_lists_every_rule_and_only_known_ones():
    rules = dict(SETTINGS.checks.rules)
    del rules["B1.NAME.002"]
    rules["B1.NAME.009"] = RuleSettings(enabled=True, severity="low")
    with pytest.raises(ConfigError, match=r"нет настроек правила B1\.NAME\.002\n.*неизвестное правило B1\.NAME\.009"):
        configured(SETTINGS.checks.model_copy(update={"rules": rules}))


def test_report_format_lists_every_rule_with_its_type_and_default_severity():
    doc = (Path(__file__).resolve().parents[3] / "docs" / "report-format.md").read_text(encoding="utf-8")
    for rule in RULES:
        severity = SEVERITY[SETTINGS.checks.rules[rule.id].severity].lower()
        assert f"| `{rule.id}` | `{rule.type}` | {severity} |" in doc, rule.id


# ---------------------------------------------------------------- documents of several files (ADR-0017)


def _sheet_of(spec_pair: tuple[DocumentInfo, Specification], sheet: int) -> tuple[DocumentInfo, Specification]:
    spec_pair[0].sheet = sheet
    return spec_pair


def test_a_specification_over_two_files_lists_the_rows_of_both():
    first = _sheet_of(spec("s0", TOP, "Изделие", [part_row()]), 1)
    second = _sheet_of(spec("s0b", TOP, "Изделие", [unit_row()]), 2)
    result = check([first, second, spec("s1", UNIT, "Узел", [])], [drawing("d1", PART, "Вал")])
    assert {n.designation for n in result.tree[0].children} == {PART, UNIT}
    assert second[0].sheet_of == "s0" and first[0].sheet_of is None
    assert "B1.DUP.003" not in {d.rule_id for d in result.discrepancies}


def test_a_later_sheet_gives_its_parameters_to_the_first():
    one, two = drawing("d1", PART, "Вал", sheet=1, sheets=2), drawing("d2", PART, "Вал", sheet=2)
    assert one.part is not None and two.part is not None
    two.part.parameters = [Parameter(id="p1", category="roughness", name="Ra", text="Ra 3,2")]
    parts = [one.part, two.part]
    assembly = assemble([one.doc, two.doc], [], parts, {"d2"})
    assert [p.document_id for p in assembly.parts] == ["d1"]
    assert [p.id for p in assembly.parts[0].parameters] == ["p1"]
    assert [d.found for d in assembly.documents] == [[1, 2]]


def test_files_of_one_sheet_number_stay_apart_as_duplicates():
    kit = [product(part_row())], [drawing("d1", PART, "Вал", sheet=1), drawing("d2", PART, "Вал", sheet=1)]
    result = check(*kit)
    assert "B1.DUP.001" in {d.rule_id for d in result.discrepancies}
    assert "B1.SHEETS.001" not in {d.rule_id for d in result.discrepancies}
    assert any("не собраны в документ" in m for m in result.log)


def test_a_file_without_a_number_is_the_first_sheet_unless_another_is():
    kit = [product(part_row())], [drawing("d1", PART, "Вал", sheets=2), drawing("d2", PART, "Вал", sheet=2)]
    assert fired(kit) == set()
    kit = [product(part_row())], [drawing("d0", PART, "Вал"), drawing("d1", PART, "Вал", sheet=1)]
    assert "B1.DUP.001" in fired(kit)


def test_a_gap_in_the_numbers_is_a_missing_sheet_even_without_the_count():
    kit = [product(part_row())], [drawing("d1", PART, "Вал", sheet=1), drawing("d3", PART, "Вал", sheet=3)]
    [gone] = [d for d in check(*kit).discrepancies if d.rule_id == "B1.SHEETS.001"]
    assert gone.actual == "1, 3" and "нет листов 2" in gone.message


def test_later_sheets_under_a_specification_designation_are_its_sheets():
    """Form 2a without a table the core reads, under the designation of a specification: its later sheets."""
    head = _sheet_of(spec("s1", UNIT, "Узел", [part_row()]), 1)
    head[1].sheets = Extracted(value=3)
    later = [drawing("d2", UNIT, "Узел", sheet=2), drawing("d3", UNIT, "Узел", sheet=3)]
    result = check([product(unit_row()), head], [drawing("d1", PART, "Вал"), *later])
    assert [ref.doc.kind for ref in later] == ["specification", "specification"]
    assert [ref.doc.sheet_of for ref in later] == ["s1", "s1"]
    assert {d.rule_id for d in result.discrepancies} == set()


def test_a_copy_named_as_a_second_sheet_without_form_2a_is_a_duplicate():
    """A system names a copy of a file «… (2)»: the name alone does not make it a later sheet."""
    kit = [product(part_row())], [drawing("d1", PART, "Вал"), drawing("d2", PART, "Вал", sheet=2)]
    assert "B1.DUP.001" in fired_with(kit, later=set())
    assert "B1.DUP.001" not in fired_with(kit, later={"d2"})


def test_a_sheet_count_read_by_ocr_is_not_trusted():
    one, two = drawing("d1", PART, "Вал", sheet=1, sheets=7), drawing("d2", PART, "Вал", sheet=2)
    assert one.part is not None and one.part.title_block.sheets is not None
    assert "B1.SHEETS.001" in fired(([product(part_row())], [one, two]))
    one.doc.sheet_of = two.doc.sheet_of = None
    assert one.part.title_block.sheets.source is not None
    one.part.title_block.sheets.source.method = "ocr"  # the 1 of the GOST lettering read as 7
    assert "B1.SHEETS.001" not in fired(([product(part_row())], [one, two]))


def fired_with(kit: Kit, later: set[str]) -> set[str | None]:
    return {d.rule_id for d in check(*kit, later=later).discrepancies}


def test_every_document_of_several_files_is_assembled():
    other = "АБВГ.100000.002"
    files = [drawing("d1", PART, "Вал", sheet=1), drawing("d2", PART, "Вал", sheet=2)]
    files += [drawing("d3", other, "Ось", sheet=1), drawing("d4", other, "Ось", sheet=2)]
    result = check([product(part_row(), row(2, "Детали", other, "Ось"))], files)
    assert [f.doc.sheet_of for f in files] == [None, "d1", None, "d3"]
    assert result.discrepancies == []
