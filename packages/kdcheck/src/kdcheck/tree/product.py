"""Block 1: the product tree.

Input: parsed specifications and drawings. Output: the product tree, with assemblies expanded recursively through
their own specifications and each position matched with the documents of the kit by designation. A position whose
document is not in the kit gets the ``missing`` status; the rules of :mod:`kdcheck.checks` report it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from kdcheck.normalize.designations import Designations
from kdcheck.schemas import DocumentInfo, Extracted, NodeKind, PartRecord, SourceRef, Specification, SpecRow, TreeNode


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
        roots = [des for des in self.spec_by_des if des not in children_des]
        if not roots and self.spec_by_des:
            roots = [next(iter(self.spec_by_des))]
            tree.log.append(
                "Не удалось определить изделие верхнего уровня (циклические ссылки) — выбрана первая спецификация"
            )
        trees = [self._assembly_node(des, None, None, 1.0, path=()) for des in roots]
        if len(trees) == 1:
            tree.roots = trees
            tree.root_designation = trees[0].designation
            tree.root_name = trees[0].name
        elif trees:
            kit = TreeNode(id=self._next_node(), designation=None, name="Комплект", kind="kit", children=trees)
            tree.roots = [kit]
            tree.log.append(f"В комплекте несколько изделий верхнего уровня: {', '.join(roots)}")
        return tree

    def _assembly_node(
        self, des: str, row: SpecRow | None, section: str | None, total: float, path: tuple[str, ...]
    ) -> TreeNode:
        specs = self.spec_by_des.get(des, [])
        node = TreeNode(
            id=self._next_node(),
            designation=des,
            kind="assembly",
            section=section,
            name=text(row.name) if row else (text(specs[0][1].name) if specs else None),
            position=text(row.position) if row else None,
            quantity=_qty(row),
            total_quantity=total if row else None,
        )
        if row is not None:
            self.tree.rows[node.id] = row
        self.node_index[des].append(node)
        self.tree.referenced.add(des)
        node.drawing_document_ids = [d.doc.id for d in self.drawings_by_des.get(f"{des} {self.sb}", [])]
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
            child = self._row_node(r, total, path + (des,))
            self.tree.parents[child.id] = node
            node.children.append(child)
        return node

    def _row_node(self, row: SpecRow, parent_total: float, path: tuple[str, ...]) -> TreeNode:
        kind = self.sections.get(row.section or "", "unknown")
        des = self.eskd.normalize_designation(text(row.designation))
        qty = _qty(row)
        total = parent_total * qty if qty is not None else parent_total
        if des:
            self.tree.referenced.add(des)
        if kind == "assembly" and des:
            return self._assembly_node(des, row, row.section, total, path)
        if kind == "unknown" and des and des in self.spec_by_des:
            return self._assembly_node(des, row, row.section, total, path)
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
        note = (text(row.note) or "").upper()
        if kind in self.needs_drawing or (kind == "unknown" and des):
            if not des:
                node.status = "not_required"
            elif self.eskd.words.no_drawing_mark in note.replace(" ", ""):
                node.status = "not_required"
            else:
                found = self.drawings_by_des.get(des, [])
                node.drawing_document_ids = [d.doc.id for d in found]
                if not found:
                    node.status = "missing"
        else:
            node.status = "not_required"
        return node


def _qty(row: SpecRow | None) -> float | None:
    if row is None or row.quantity is None:
        return None
    v = row.quantity.value
    return float(v) if isinstance(v, (int, float)) else None


def build_tree(
    specs: list[tuple[DocumentInfo, Specification]], drawings: list[DrawingRef], eskd: Designations
) -> ProductTree:
    return _Builder(specs, drawings, eskd).build()
