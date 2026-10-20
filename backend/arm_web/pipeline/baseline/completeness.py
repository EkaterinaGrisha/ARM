"""Блок 1: дерево изделия и проверка комплектности.

На входе — разобранные спецификации и чертежи, на выходе — дерево изделия
(сборочные единицы раскрываются рекурсивно по своим спецификациям) и перечень
расхождений с классификацией по типу.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ...models import Discrepancy, DocumentInfo, PartRecord, SourceRef, Specification, SpecRow, TreeNode
from .designations import base_designation, doc_code, litera_rank, normalize_designation, normalize_name

SECTION_KIND = {
    "Документация": "document",
    "Комплексы": "assembly",
    "Сборочные единицы": "assembly",
    "Детали": "part",
    "Стандартные изделия": "standard",
    "Прочие изделия": "other_item",
    "Материалы": "material",
    "Комплекты": "other_item",
}
NEEDS_DRAWING = {"part", "document"}


@dataclass
class DrawingRef:
    doc: DocumentInfo
    part: PartRecord | None

    @property
    def designation(self) -> str | None:
        return self.doc.designation

    def tb_value(self, key: str):
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
class Result:
    tree: list[TreeNode] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    root_designation: str | None = None
    root_name: str | None = None
    log: list[str] = field(default_factory=list)


def _value(ex) -> str | None:
    return str(ex.value) if ex is not None and ex.value not in (None, "") else None


class Checker:
    def __init__(self, specs: list[tuple[DocumentInfo, Specification]], drawings: list[DrawingRef]):
        self.specs = specs
        self.drawings = drawings
        self.result = Result()
        self._ids = 0
        self._node_ids = 0
        self.spec_by_des: dict[str, list[tuple[DocumentInfo, Specification]]] = defaultdict(list)
        for doc, spec in specs:
            des = normalize_designation(_value(spec.designation)) or doc.designation
            if des:
                self.spec_by_des[des].append((doc, spec))
        self.drawings_by_des: dict[str, list[DrawingRef]] = defaultdict(list)
        for d in drawings:
            if d.designation:
                self.drawings_by_des[d.designation].append(d)
        self.referenced: set[str] = set()
        self.node_index: dict[str, list[TreeNode]] = defaultdict(list)

    # ---------------------------------------------------------------- helpers
    def _next_id(self) -> str:
        self._ids += 1
        return f"x{self._ids}"

    def _next_node(self) -> str:
        self._node_ids += 1
        return f"n{self._node_ids}"

    def add(self, type_: str, severity: str, designation: str | None, title: str, message: str, *,
            expected: str | None = None, actual: str | None = None, docs: list[str] | None = None,
            nodes: list[TreeNode] | None = None, sources: list[SourceRef | None] | None = None) -> Discrepancy:
        disc = Discrepancy(
            id=self._next_id(), type=type_, severity=severity, designation=designation, title=title, message=message,
            expected=expected, actual=actual, document_ids=sorted(set(docs or [])),
            tree_node_ids=[n.id for n in nodes or []], sources=[s for s in sources or [] if s is not None],
        )
        for n in nodes or []:
            n.discrepancy_ids.append(disc.id)
            if n.status == "ok":
                n.status = "warning"
        self.result.discrepancies.append(disc)
        return disc

    # ---------------------------------------------------------------- tree
    def run(self) -> Result:
        if not self.specs:
            self.result.log.append("Спецификации не найдены — дерево изделия не построено, проверка комплектности ограничена")
        children_des = {
            normalize_designation(_value(r.designation))
            for _, spec in self.specs
            for r in spec.rows
            if r.designation is not None
        }
        roots = [des for des in self.spec_by_des if des not in children_des]
        if not roots and self.spec_by_des:
            roots = [next(iter(self.spec_by_des))]
            self.result.log.append("Не удалось определить изделие верхнего уровня (циклические ссылки) — выбрана первая спецификация")
        trees = [self._assembly_node(des, None, None, 1.0, path=()) for des in roots]
        if len(trees) == 1:
            self.result.tree = trees
            self.result.root_designation = trees[0].designation
            self.result.root_name = trees[0].name
        elif trees:
            kit = TreeNode(id=self._next_node(), designation=None, name="Комплект", kind="kit", children=trees)
            self.result.tree = [kit]
            self.result.log.append(f"В комплекте несколько изделий верхнего уровня: {', '.join(roots)}")
        self._check_orphans()
        self._check_duplicates()
        self._check_names()
        self._check_litera()
        return self.result

    def _assembly_node(self, des: str, row: SpecRow | None, section: str | None, total: float, path: tuple[str, ...]) -> TreeNode:
        specs = self.spec_by_des.get(des, [])
        node = TreeNode(
            id=self._next_node(), designation=des, kind="assembly", section=section,
            name=_value(row.name) if row else (_value(specs[0][1].name) if specs else None),
            position=_value(row.position) if row else None,
            quantity=_qty(row), total_quantity=total if row else None,
        )
        self.node_index[des].append(node)
        self.referenced.add(des)
        node.drawing_document_ids = [d.doc.id for d in self.drawings_by_des.get(f"{des} СБ", [])]
        if not specs:
            node.status = "missing"
            has_sb = bool(node.drawing_document_ids)
            self.add(
                "missing_specification", "high", des, "Нет спецификации сборочной единицы",
                f"Сборочная единица {des} указана в спецификации, но её спецификация в комплекте отсутствует"
                + ("; сборочный чертёж есть, состав раскрыть невозможно" if has_sb else "; сборочного чертежа также нет"),
                docs=node.drawing_document_ids, nodes=[node], sources=[row.source if row else None],
            )
            return node
        if des in path:
            self.result.log.append(f"Циклическая ссылка на {des} — раскрытие остановлено")
            return node
        doc, spec = specs[0]
        node.spec_document_id = doc.id
        if node.name is None:
            node.name = _value(spec.name)
        for r in spec.rows:
            node.children.append(self._row_node(r, total, path + (des,), parent=node))
        return node

    def _row_node(self, row: SpecRow, parent_total: float, path: tuple[str, ...], parent: TreeNode) -> TreeNode:
        kind = SECTION_KIND.get(row.section or "", "unknown")
        des = normalize_designation(_value(row.designation))
        qty = _qty(row)
        total = parent_total * qty if qty is not None else parent_total
        if des:
            self.referenced.add(des)
        if kind == "assembly" and des:
            return self._assembly_node(des, row, row.section, total, path)
        if kind == "unknown" and des and des in self.spec_by_des:
            return self._assembly_node(des, row, row.section, total, path)
        node = TreeNode(
            id=self._next_node(), designation=des, name=_value(row.name), kind=kind, section=row.section,
            position=_value(row.position), quantity=qty, total_quantity=total if qty is not None else None,
        )
        if des:
            self.node_index[des].append(node)
        note = (_value(row.note) or "").upper()
        if kind in NEEDS_DRAWING or (kind == "unknown" and des):
            if not des:
                node.status = "not_required"
            elif "БЧ" in note.replace(" ", ""):
                node.status = "not_required"
            else:
                found = self.drawings_by_des.get(des, [])
                node.drawing_document_ids = [d.doc.id for d in found]
                if not found:
                    node.status = "missing"
                    what = "Документ" if kind == "document" else "Чертёж детали"
                    self.add(
                        "missing_drawing", "high", des, f"{what} отсутствует",
                        f"{what} {des} «{_value(row.name) or '—'}» указан в спецификации {path[-1]}, но в комплекте не найден",
                        expected=des, docs=[parent.spec_document_id] if parent.spec_document_id else [],
                        nodes=[node], sources=[row.source],
                    )
        else:
            node.status = "not_required"
        return node

    # ---------------------------------------------------------------- checks
    def _check_orphans(self) -> None:
        if not self.specs:
            return
        for des, refs in self.drawings_by_des.items():
            base, code = base_designation(des), doc_code(des)
            if des in self.referenced or (code == "СБ" and base in self.referenced):
                continue
            for ref in refs:
                self.add(
                    "orphan_drawing", "medium", des, "Чертёж не входит ни в одну спецификацию",
                    f"Документ {des} «{ref.doc.name or '—'}» ({ref.doc.path}) есть в комплекте, "
                    "но не указан ни в одной спецификации",
                    actual=ref.doc.path, docs=[ref.doc.id], sources=[ref.tb_source("designation")],
                )

    def _check_duplicates(self) -> None:
        for des, refs in self.drawings_by_des.items():
            if len(refs) < 2:
                continue
            sheets = [ref.tb_value("sheet") for ref in refs]
            if all(s is not None for s in sheets) and len(set(sheets)) == len(sheets):
                continue  # многолистовой документ, разбитый на файлы
            files = ", ".join(r.doc.path for r in refs)
            self.add(
                "duplicate_designation", "high", des, "Обозначение встречается в нескольких документах",
                f"Обозначение {des} присвоено {len(refs)} документам: {files}",
                actual=files, docs=[r.doc.id for r in refs], nodes=self.node_index.get(des, []),
                sources=[r.tb_source("designation") for r in refs],
            )
            revisions = {str(r.tb_value("revision") or "—") for r in refs}
            literas = {str(r.tb_value("litera") or "—") for r in refs}
            if len(revisions) > 1 or len(literas) > 1:
                parts = [
                    f"{r.doc.path}: изм. {r.tb_value('revision') or '—'}, лит. {r.tb_value('litera') or '—'}"
                    for r in refs
                ]
                self.add(
                    "revision_mismatch", "medium", des, "Разные версии одного документа",
                    f"В комплекте несколько версий {des} с разным номером изменения или литерой: " + "; ".join(parts),
                    actual="; ".join(parts), docs=[r.doc.id for r in refs], nodes=self.node_index.get(des, []),
                    sources=[r.tb_source("revision") or r.tb_source("litera") for r in refs],
                )
        names: dict[str, dict[str, list[tuple[DocumentInfo, SpecRow]]]] = defaultdict(lambda: defaultdict(list))
        for doc, spec in self.specs:
            for r in spec.rows:
                des = normalize_designation(_value(r.designation))
                if des:
                    names[des][normalize_name(_value(r.name))].append((doc, r))
        for des, by_name in names.items():
            if len(by_name) > 1:
                variants = [f"«{_value(rows[0][1].name)}» ({rows[0][0].path})" for rows in by_name.values()]
                self.add(
                    "duplicate_designation", "high", des, "Одно обозначение у разных позиций",
                    f"Обозначение {des} указано в спецификациях с разными наименованиями: " + ", ".join(variants),
                    docs=[d.id for rows in by_name.values() for d, _ in rows], nodes=self.node_index.get(des, []),
                    sources=[r.source for rows in by_name.values() for _, r in rows],
                )
        for des, items in self.spec_by_des.items():
            if len(items) > 1:
                files = ", ".join(d.path for d, _ in items)
                self.add(
                    "duplicate_designation", "high", des, "Несколько спецификаций с одним обозначением",
                    f"Обозначение {des} присвоено {len(items)} спецификациям: {files}",
                    docs=[d.id for d, _ in items], sources=[s.designation.source if s.designation else None for _, s in items],
                )

    def _check_names(self) -> None:
        for des, nodes in self.node_index.items():
            for node in nodes:
                expected = node.name
                if not expected or node.kind == "document":
                    continue
                candidates = list(self.drawings_by_des.get(des, []))
                if node.kind == "assembly":
                    candidates = list(self.drawings_by_des.get(f"{des} СБ", []))
                for ref in candidates:
                    actual = ref.tb_value("name") or ref.doc.name
                    if not actual or normalize_name(actual) == normalize_name(expected):
                        continue
                    self.add(
                        "name_mismatch", "medium", des, "Наименование на чертеже не совпадает со спецификацией",
                        f"{des}: в спецификации «{expected}», на чертеже «{actual}»",
                        expected=expected, actual=str(actual), docs=[ref.doc.id], nodes=[node],
                        sources=[ref.tb_source("name")],
                    )
                if node.kind == "assembly" and node.spec_document_id:
                    for _, spec in self.spec_by_des.get(des, [])[:1]:
                        own = _value(spec.name)
                        if own and normalize_name(own) != normalize_name(expected):
                            self.add(
                                "name_mismatch", "medium", des, "Наименование спецификации не совпадает с позицией",
                                f"{des}: в вышестоящей спецификации «{expected}», в собственной спецификации «{own}»",
                                expected=expected, actual=own, docs=[node.spec_document_id], nodes=[node],
                                sources=[spec.name.source if spec.name else None],
                            )

    def _check_litera(self) -> None:
        """Литера спецификации и её сборочного чертежа должны совпадать; литера составной части
        не может быть ниже литеры сборочной единицы (ГОСТ 2.103)."""
        for des, items in self.spec_by_des.items():
            doc, spec = items[0]
            spec_lit = _value(spec.litera)
            for ref in self.drawings_by_des.get(f"{des} СБ", []):
                sb_lit = ref.tb_value("litera")
                if spec_lit and sb_lit and spec_lit != sb_lit:
                    self.add(
                        "revision_mismatch", "medium", des, "Литера спецификации и сборочного чертежа различается",
                        f"{des}: литера спецификации «{spec_lit}», сборочного чертежа «{sb_lit}»",
                        expected=spec_lit, actual=str(sb_lit), docs=[doc.id, ref.doc.id],
                        nodes=self.node_index.get(des, []),
                        sources=[spec.litera.source if spec.litera else None, ref.tb_source("litera")],
                    )
            rank = litera_rank(spec_lit)
            if rank is None:
                continue
            for row in spec.rows:
                child = normalize_designation(_value(row.designation))
                if not child or doc_code(child):
                    continue
                child_docs: list[tuple[str | None, str, SourceRef | None]] = []
                for ref in self.drawings_by_des.get(child, []):
                    child_docs.append((ref.tb_value("litera"), ref.doc.id, ref.tb_source("litera")))
                for cdoc, cspec in self.spec_by_des.get(child, []):
                    child_docs.append((_value(cspec.litera), cdoc.id, cspec.litera.source if cspec.litera else None))
                for lit, doc_id, src in child_docs:
                    crank = litera_rank(str(lit) if lit else None)
                    if crank is not None and crank < rank:
                        self.add(
                            "revision_mismatch", "medium", child, "Литера составной части ниже литеры изделия",
                            f"{child}: литера «{lit}» ниже литеры сборочной единицы {des} «{spec_lit}»",
                            expected=f"не ниже {spec_lit}", actual=str(lit), docs=[doc_id, doc.id],
                            nodes=self.node_index.get(child, []), sources=[src, row.source],
                        )


def _qty(row: SpecRow | None) -> float | None:
    if row is None or row.quantity is None:
        return None
    v = row.quantity.value
    return float(v) if isinstance(v, (int, float)) else None


def check(specs: list[tuple[DocumentInfo, Specification]], drawings: list[DrawingRef]) -> Result:
    return Checker(specs, drawings).run()
