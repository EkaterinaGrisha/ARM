"""Block 1 on synthetic structures: no PDFs, only the tree and the check logic."""

from typing import NamedTuple

from kdcheck.checks.completeness import check as run_check
from kdcheck.config import load_settings
from kdcheck.normalize.designations import Designations
from kdcheck.schemas import (
    Discrepancy,
    DocumentInfo,
    Extracted,
    PartRecord,
    Specification,
    SpecRow,
    TitleBlock,
    TreeNode,
)
from kdcheck.tree.product import DrawingRef, build_tree

ESKD = Designations(load_settings().dictionaries)


class Result(NamedTuple):
    tree: list[TreeNode]
    discrepancies: list[Discrepancy]
    log: list[str]


def check(specs, drawings) -> Result:
    tree = build_tree(specs, drawings, ESKD)
    return Result(tree.roots, run_check(tree, ESKD), tree.log)


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
    doc_id: str, des: str, name: str, litera: str = "О1", revision: int | None = None, sheet: int | None = None
):
    info = DocumentInfo(
        id=doc_id, file_name=f"{doc_id}.pdf", path=f"{doc_id}.pdf", kind="part_drawing", designation=des, name=name
    )
    tb = TitleBlock(
        designation=Extracted(value=des),
        name=Extracted(value=name),
        litera=Extracted(value=litera),
        revision=Extracted(value=revision) if revision else None,
        sheet=Extracted(value=sheet) if sheet else None,
    )
    return DrawingRef(doc=info, part=PartRecord(document_id=doc_id, designation=des, name=name, title_block=tb))


def types(result) -> set[tuple[str, str]]:
    return {(d.type, d.designation) for d in result.discrepancies}


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
    assert types(result) == {
        ("missing_specification", "АБВГ.100000.200"),
        ("name_mismatch", "АБВГ.100000.001"),
        ("revision_mismatch", "АБВГ.100000.101"),
        ("orphan_drawing", "АБВГ.999999.001"),
        ("duplicate_designation", "АБВГ.100000.002"),
        ("revision_mismatch", "АБВГ.100000.002"),
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
    assert types(result) == {("missing_drawing", "АБВГ.100000.001")}
    missing = next(n for n in result.tree[0].children if n.designation == "АБВГ.100000.001")
    assert missing.status == "missing" and missing.discrepancy_ids


def test_no_specifications_is_reported_not_crashed():
    result = check([], [drawing("d1", "АБВГ.100000.001", "Вал")])
    assert result.tree == []
    assert result.discrepancies == []
    assert result.log
