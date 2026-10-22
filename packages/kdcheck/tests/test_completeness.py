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
    PartRecord,
    Specification,
    SpecRow,
    TitleBlock,
    TreeNode,
)
from kdcheck.tree.product import DrawingRef, build_tree

SETTINGS = load_settings()
ESKD = Designations(SETTINGS.dictionaries)


class Result(NamedTuple):
    tree: list[TreeNode]
    discrepancies: list[Discrepancy]
    log: list[str]


def check(specs, drawings, checks: Checks = SETTINGS.checks) -> Result:
    tree = build_tree(specs, drawings, ESKD)
    return Result(tree.roots, run_check(tree, ESKD, configured(checks)), tree.log)


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
Kit = tuple[list[tuple[DocumentInfo, Specification]], list[DrawingRef]]


def product(*rows: SpecRow, litera: str = "О1") -> tuple[DocumentInfo, Specification]:
    return spec("s0", TOP, "Изделие", list(rows), litera=litera)


def part_row(name: str = "Вал", note: str = "") -> SpecRow:
    return row(1, "Детали", PART, name, note=note)


def unit_row() -> SpecRow:
    return row(2, "Сборочные единицы", UNIT, "Узел")


def drawing_row() -> SpecRow:
    return row(3, "Документация", f"{TOP} СБ", "Сборочный чертёж", None)


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


def test_rule_ids_are_unique_and_cover_every_discrepancy_type():
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"B1\.[A-Z]+\.\d{3}", rule_id) for rule_id in ids)
    assert {rule.type for rule in RULES} == set(get_args(DiscrepancyType))


def test_settings_turn_a_rule_off_and_set_its_severity():
    kit = CASES["B1.NAME.001"][0]()
    rules = SETTINGS.checks.rules
    off = Checks(rules={**rules, "B1.NAME.001": RuleSettings(enabled=False, severity="medium")})
    assert "B1.NAME.001" not in fired(kit, off)
    low = Checks(rules={**rules, "B1.NAME.001": RuleSettings(enabled=True, severity="low")})
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
        configured(Checks(rules=rules))


def test_report_format_lists_every_rule_with_its_type_and_default_severity():
    doc = (Path(__file__).resolve().parents[3] / "docs" / "report-format.md").read_text(encoding="utf-8")
    for rule in RULES:
        severity = SEVERITY[SETTINGS.checks.rules[rule.id].severity].lower()
        assert f"| `{rule.id}` | `{rule.type}` | {severity} |" in doc, rule.id
