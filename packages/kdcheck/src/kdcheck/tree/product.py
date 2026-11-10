"""Block 1: the product tree.

Input: parsed specifications and drawings. Output: the product tree, with assemblies expanded recursively through
their own specifications and each position matched with the documents of the kit by designation. A position whose
document is not in the kit gets the ``missing`` status; the rules of :mod:`kdcheck.checks` report it.

A variant «…-01» without a specification of its own is expanded through the group specification of its base
designation by variant B of GOST 2.113 (ADR-0025): its positions are the rows with a quantity in its column, and
the quantity is that one. The base variant is the column «–». A row of a group specification without any quantity
read belongs to every variant: a missed cell must not hide a position. A variant of a part without a drawing of its
own is drawn on the group drawing of its base, which writes «См. табл.» for the mass (GOST 2.113, 4.1.10).

By variant A the same group specification holds the constant data, common to every variant, and the variable data
of each variant under its designation (ADR-0026): a variant takes the constant rows and its own. The rows of the
other variants stay listed in a specification, so their documents are not ones that no specification names.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from kdcheck.normalize.designations import BASE_VARIANT, Designations, split_variant, variant_designation
from kdcheck.schemas import DocumentInfo, Extracted, NodeKind, PartRecord, SourceRef, Specification, SpecRow, TreeNode
from kdcheck.tree.documents import SheetSet


@dataclass
class DrawingRef:
    doc: DocumentInfo
    part: PartRecord | None

    @property
    def designation(self) -> str | None:
        return self.doc.designation

    def tb_value(self, key: str) -> str | float | int | None:
        if self.part is None:
            return None
        ex = getattr(self.part.title_block, key, None)
        return ex.value if ex is not None else None

    def tb_source(self, key: str) -> SourceRef | None:
        if self.part is None:
            return None
        ex = getattr(self.part.title_block, key, None)
        return ex.source if ex is not None else None


@dataclass
class ProductTree:
    """The tree and the indexes the rules look things up in."""

    specs: list[tuple[DocumentInfo, Specification]]
    drawings: list[DrawingRef]
    roots: list[TreeNode] = field(default_factory=list)
    root_designation: str | None = None
    root_name: str | None = None
    log: list[str] = field(default_factory=list)
    spec_by_des: dict[str, list[tuple[DocumentInfo, Specification]]] = field(default_factory=dict)
    drawings_by_des: dict[str, list[DrawingRef]] = field(default_factory=dict)
    node_index: dict[str, list[TreeNode]] = field(default_factory=dict)  # designation → its nodes
    referenced: set[str] = field(default_factory=set)  # designations the specifications name
    rows: dict[str, SpecRow] = field(default_factory=dict)  # node id → the specification row of the node
    parents: dict[str, TreeNode] = field(default_factory=dict)  # node id → the assembly that lists the node
    documents: list[SheetSet] = field(default_factory=list)  # the documents of the kit with their sheets

    def nodes(self) -> Iterator[TreeNode]:
        """Every node, depth first, each before its children."""
        return _walk(self.roots)


def _walk(nodes: Iterable[TreeNode]) -> Iterator[TreeNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


def text(ex: Extracted | None) -> str | None:
    """The value as text; None when there is none."""
    return str(ex.value) if ex is not None and ex.value not in (None, "") else None


class _Builder:
    def __init__(
        self, specs: list[tuple[DocumentInfo, Specification]], drawings: list[DrawingRef], eskd: Designations
    ) -> None:
        self.eskd = eskd
        self.sections: dict[str, NodeKind] = eskd.words.spec_sections
        self.needs_drawing = set(eskd.words.needs_drawing)
        self.sb = eskd.words.assembly_drawing_code
        self._node_ids = 0
        self.spec_by_des: defaultdict[str, list[tuple[DocumentInfo, Specification]]] = defaultdict(list)
        for doc, spec in specs:
            des = eskd.normalize_designation(text(spec.designation)) or doc.designation
            if des:
                self.spec_by_des[des].append((doc, spec))
        self.variants: dict[str, tuple[str, str]] = {}  # a variant of a group specification → its base and number
        for des, found in self.spec_by_des.items():
            for number in found[0][1].variants:
                if number != BASE_VARIANT:
                    self.variants.setdefault(variant_designation(des, number), (des, number))
        self.drawings_by_des: defaultdict[str, list[DrawingRef]] = defaultdict(list)
        for d in drawings:
            if d.designation:
                self.drawings_by_des[d.designation].append(d)
        self.node_index: defaultdict[str, list[TreeNode]] = defaultdict(list)
        self.tree = ProductTree(
            specs=specs,
            drawings=drawings,
            spec_by_des=self.spec_by_des,
            drawings_by_des=self.drawings_by_des,
            node_index=self.node_index,
        )

    def _next_node(self) -> str:
        self._node_ids += 1
        return f"n{self._node_ids}"

    def build(self) -> ProductTree:
        tree = self.tree
        if not tree.specs:
            tree.log.append("Спецификации не найдены — дерево изделия не построено, проверка комплектности ограничена")
        children_des = {
            self.eskd.normalize_designation(text(r.designation))
            for _, spec in tree.specs
            for r in spec.rows
            if r.designation is not None
        }
        named = children_des | {self.variants[d][0] for d in children_des if d in self.variants}
        roots = [des for des in self.spec_by_des if des not in named]
        if not roots and self.spec_by_des:
            roots = [next(iter(self.spec_by_des))]
            tree.log.append(
                "Не удалось определить изделие верхнего уровня (циклические ссылки) — выбрана первая спецификация"
            )
        trees = [self._assembly_node(des, None, None, None, 1.0, path=()) for des in roots]
        if len(trees) == 1:
            tree.roots = trees
            tree.root_designation = trees[0].designation
            tree.root_name = trees[0].name
        elif trees:
            kit = TreeNode(id=self._next_node(), designation=None, name="Комплект", kind="kit", children=trees)
            tree.roots = [kit]
            tree.log.append(f"В комплекте несколько изделий верхнего уровня: {', '.join(roots)}")
        return tree

    def _specs(self, des: str) -> tuple[list[tuple[DocumentInfo, Specification]], str | None, str]:
        """The specifications of an assembly, the number of its variant in a group one (None in a single one) and the
        designation of that group one."""
        if des in self.spec_by_des:  # the base variant: its column «–», or every row when there is none
            specs = self.spec_by_des[des]
            return specs, (BASE_VARIANT if BASE_VARIANT in specs[0][1].variants else None), des
        if des in self.variants:
            base, number = self.variants[des]
            return self.spec_by_des[base], number, base
        return [], None, des

    def _assembly_node(
        self,
        des: str,
        row: SpecRow | None,
        quantity: float | None,
        section: str | None,
        total: float,
        path: tuple[str, ...],
    ) -> TreeNode:
        specs, number, group = self._specs(des)
        node = TreeNode(
            id=self._next_node(),
            designation=des,
            kind="assembly",
            section=section,
            name=text(row.name) if row else (text(specs[0][1].name) if specs else None),
            position=text(row.position) if row else None,
            quantity=quantity,
            total_quantity=total if row else None,
        )
        if row is not None:
            self.tree.rows[node.id] = row
        self.node_index[des].append(node)
        self.tree.referenced.update({des, group})
        # a variant has the group assembly drawing of its base when it has none of its own (GOST 2.113)
        drawings = self.drawings_by_des.get(f"{des} {self.sb}") or self.drawings_by_des.get(f"{group} {self.sb}", [])
        node.drawing_document_ids = [d.doc.id for d in drawings]
        if not specs:
            node.status = "missing"
            return node
        if des in path:
            self.tree.log.append(f"Циклическая ссылка на {des} — раскрытие остановлено")
            return node
        doc, spec = specs[0]
        node.spec_document_id = doc.id
        if node.name is None:
            node.name = text(spec.name)
        for r in spec.rows:
            other = number is not None and bool(r.quantities) and number not in r.quantities  # by variant B
            if other or (r.variant is not None and r.variant != (number or BASE_VARIANT)):  # by variant A
                if listed := self.eskd.normalize_designation(text(r.designation)):
                    self.tree.referenced.add(listed)
                continue  # a position of other variants of the group specification
            child = self._row_node(r, number, total, path + (des,))
            self.tree.parents[child.id] = node
            node.children.append(child)
        return node

    def _row_node(self, row: SpecRow, variant: str | None, parent_total: float, path: tuple[str, ...]) -> TreeNode:
        kind = self.sections.get(row.section or "", "unknown")
        des = self.eskd.normalize_designation(text(row.designation))
        qty = _qty(row, variant)
        total = parent_total * qty if qty is not None else parent_total
        if des:
            self.tree.referenced.add(des)
        if kind == "assembly" and des:
            return self._assembly_node(des, row, qty, row.section, total, path)
        if kind == "unknown" and des and (des in self.spec_by_des or des in self.variants):
            return self._assembly_node(des, row, qty, row.section, total, path)
        node = TreeNode(
            id=self._next_node(),
            designation=des,
            name=text(row.name),
            kind=kind,
            section=row.section,
            position=text(row.position),
            quantity=qty,
            total_quantity=total if qty is not None else None,
        )
        self.tree.rows[node.id] = row
        if des:
            self.node_index[des].append(node)
        if kind in self.needs_drawing or (kind == "unknown" and des):
            if not des or self._no_drawing(row):
                node.status = "not_required"
            else:
                found = self.drawings_by_des.get(des, []) or self._group_drawings(des)
                node.drawing_document_ids = [d.doc.id for d in found]
                if not found:
                    node.status = "missing"
        else:
            node.status = "not_required"
        return node

    def _no_drawing(self, row: SpecRow) -> bool:
        """Whether the row marks a part made without a drawing: «БЧ» in the format column, as GOST 2.106 writes it,
        or in the note, as some makers do."""
        note = self.eskd.cyrillic((text(row.note) or "").upper()).replace(" ", "")
        return self.eskd.no_drawing(text(row.format)) or self.eskd.words.no_drawing_mark in note

    def _group_drawings(self, des: str) -> list[DrawingRef]:
        """The group drawing of the base of the variant ``des``: the drawing of the base that writes «См. табл.» for
        the mass; none for a base variant or without such a drawing."""
        base, number = split_variant(des)
        if number == BASE_VARIANT:
            return []
        group = self.eskd.words.see_table.name
        found = [d for d in self.drawings_by_des.get(base, []) if d.tb_value("mass") == group]
        if found:
            self.tree.referenced.add(base)
        return found


def _qty(row: SpecRow, variant: str | None) -> float | None:
    """The quantity of a row, of the ``variant`` in a group specification."""
    ex = row.quantities.get(variant) if variant is not None and row.quantities else row.quantity
    v = ex.value if ex is not None else None
    return float(v) if isinstance(v, (int, float)) else None


def build_tree(
    specs: list[tuple[DocumentInfo, Specification]],
    drawings: list[DrawingRef],
    eskd: Designations,
    documents: list[SheetSet] | None = None,
) -> ProductTree:
    """The tree of the specifications and drawings of the kit, one of each per document of its sheets."""
    tree = _Builder(specs, drawings, eskd).build()
    tree.documents = documents or []
    return tree
