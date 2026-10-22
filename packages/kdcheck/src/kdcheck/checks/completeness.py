"""Block 1: completeness checks over the product tree.

Input: the product tree of :mod:`kdcheck.tree.product` and its indexes. Output: the discrepancies, each found by a
rule of :mod:`kdcheck.checks.rules`, classified by type and linked to the tree nodes and to the documents and page
regions they come from. A rule turned off in ``configs/checks.yaml`` reports nothing.
"""

from __future__ import annotations

from collections import defaultdict

from kdcheck.checks.rules import ActiveRule
from kdcheck.normalize.designations import Designations, base_designation, doc_code
from kdcheck.schemas import Discrepancy, DocumentInfo, SourceRef, SpecRow, TreeNode
from kdcheck.tree.product import ProductTree, text


class Checker:
    def __init__(self, tree: ProductTree, eskd: Designations, rules: dict[str, ActiveRule]) -> None:
        self.tree = tree
        self.eskd = eskd
        self.rules = rules
        self.sb = eskd.words.assembly_drawing_code
        self.discrepancies: list[Discrepancy] = []
        self._ids = 0

    def _next_id(self) -> str:
        self._ids += 1
        return f"x{self._ids}"

    def add(
        self,
        rule_id: str,
        designation: str | None,
        message: str,
        *,
        expected: str | None = None,
        actual: str | None = None,
        docs: list[str] | None = None,
        nodes: list[TreeNode] | None = None,
        sources: list[SourceRef | None] | None = None,
    ) -> None:
        active = self.rules[rule_id]
        if not active.settings.enabled:
            return
        disc = Discrepancy(
            id=self._next_id(),
            rule_id=rule_id,
            type=active.rule.type,
            severity=active.settings.severity,
            designation=designation,
            title=active.rule.title,
            message=message,
            expected=expected,
            actual=actual,
            document_ids=sorted(set(docs or [])),
            tree_node_ids=[n.id for n in nodes or []],
            sources=[s for s in sources or [] if s is not None],
        )
        for n in nodes or []:
            n.discrepancy_ids.append(disc.id)
            if n.status == "ok":
                n.status = "warning"
        self.discrepancies.append(disc)

    def run(self) -> list[Discrepancy]:
        self._check_missing()
        self._check_orphans()
        self._check_duplicates()
        self._check_names()
        self._check_litera()
        return self.discrepancies

    def _check_missing(self) -> None:
        """Positions whose document is not in the kit, in the order of the tree."""
        for node in self.tree.nodes():
            if node.status != "missing":
                continue
            row = self.tree.rows.get(node.id)
            if node.kind == "assembly":
                self.add(
                    "B1.SPEC.001",
                    node.designation,
                    f"Сборочная единица {node.designation} указана в спецификации, но её спецификация в комплекте "
                    "отсутствует"
                    + (
                        "; сборочный чертёж есть, состав раскрыть невозможно"
                        if node.drawing_document_ids
                        else "; сборочного чертежа также нет"
                    ),
                    docs=node.drawing_document_ids,
                    nodes=[node],
                    sources=[row.source if row else None],
                )
                continue
            parent = self.tree.parents[node.id]
            document = node.kind == "document"
            what = "Документ" if document else "Чертёж детали"
            self.add(
                "B1.MISSING.002" if document else "B1.MISSING.001",
                node.designation,
                f"{what} {node.designation} «{node.name or '—'}» указан в спецификации {parent.designation}, "
                "но в комплекте не найден",
                expected=node.designation,
                docs=[parent.spec_document_id] if parent.spec_document_id else [],
                nodes=[node],
                sources=[row.source if row else None],
            )

    def _check_orphans(self) -> None:
        tree = self.tree
        if not tree.specs:
            return
        for des, refs in tree.drawings_by_des.items():
            base, code = base_designation(des), doc_code(des)
            if des in tree.referenced or (code == self.sb and base in tree.referenced):
                continue
            for ref in refs:
                self.add(
                    "B1.ORPHAN.001",
                    des,
                    f"Документ {des} «{ref.doc.name or '—'}» ({ref.doc.path}) есть в комплекте, "
                    "но не указан ни в одной спецификации",
                    actual=ref.doc.path,
                    docs=[ref.doc.id],
                    sources=[ref.tb_source("designation")],
                )

    def _check_duplicates(self) -> None:
        tree = self.tree
        for des, refs in tree.drawings_by_des.items():
            if len(refs) < 2:
                continue
            sheets = [ref.tb_value("sheet") for ref in refs]
            if all(s is not None for s in sheets) and len(set(sheets)) == len(sheets):
                continue  # a multi-sheet document split into files
            files = ", ".join(r.doc.path for r in refs)
            self.add(
                "B1.DUP.001",
                des,
                f"Обозначение {des} присвоено {len(refs)} документам: {files}",
                actual=files,
                docs=[r.doc.id for r in refs],
                nodes=tree.node_index.get(des, []),
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
                    "B1.VERSION.001",
                    des,
                    f"В комплекте несколько версий {des} с разным номером изменения или литерой: " + "; ".join(parts),
                    actual="; ".join(parts),
                    docs=[r.doc.id for r in refs],
                    nodes=tree.node_index.get(des, []),
                    sources=[r.tb_source("revision") or r.tb_source("litera") for r in refs],
                )
        names: dict[str, dict[str, list[tuple[DocumentInfo, SpecRow]]]] = defaultdict(lambda: defaultdict(list))
        for doc, spec in tree.specs:
            for r in spec.rows:
                row_des = self.eskd.normalize_designation(text(r.designation))
                if row_des:
                    names[row_des][self.eskd.normalize_name(text(r.name))].append((doc, r))
        for des, by_name in names.items():
            if len(by_name) > 1:
                variants = [f"«{text(rows[0][1].name)}» ({rows[0][0].path})" for rows in by_name.values()]
                self.add(
                    "B1.DUP.002",
                    des,
                    f"Обозначение {des} указано в спецификациях с разными наименованиями: " + ", ".join(variants),
                    docs=[d.id for rows in by_name.values() for d, _ in rows],
                    nodes=tree.node_index.get(des, []),
                    sources=[r.source for rows in by_name.values() for _, r in rows],
                )
        for des, items in tree.spec_by_des.items():
            if len(items) > 1:
                files = ", ".join(d.path for d, _ in items)
                self.add(
                    "B1.DUP.003",
                    des,
                    f"Обозначение {des} присвоено {len(items)} спецификациям: {files}",
                    docs=[d.id for d, _ in items],
                    sources=[s.designation.source if s.designation else None for _, s in items],
                )

    def _check_names(self) -> None:
        tree = self.tree
        for des, nodes in tree.node_index.items():
            for node in nodes:
                expected = node.name
                if not expected or node.kind == "document":
                    continue
                candidates = list(tree.drawings_by_des.get(des, []))
                if node.kind == "assembly":
                    candidates = list(tree.drawings_by_des.get(f"{des} {self.sb}", []))
                for ref in candidates:
                    actual = ref.tb_value("name") or ref.doc.name
                    if not actual or self.eskd.normalize_name(str(actual)) == self.eskd.normalize_name(expected):
                        continue
                    self.add(
                        "B1.NAME.001",
                        des,
                        f"{des}: в спецификации «{expected}», на чертеже «{actual}»",
                        expected=expected,
                        actual=str(actual),
                        docs=[ref.doc.id],
                        nodes=[node],
                        sources=[ref.tb_source("name")],
                    )
                if node.kind == "assembly" and node.spec_document_id:
                    for _, spec in tree.spec_by_des.get(des, [])[:1]:
                        own = text(spec.name)
                        if own and self.eskd.normalize_name(own) != self.eskd.normalize_name(expected):
                            self.add(
                                "B1.NAME.002",
                                des,
                                f"{des}: в вышестоящей спецификации «{expected}», в собственной спецификации «{own}»",
                                expected=expected,
                                actual=own,
                                docs=[node.spec_document_id],
                                nodes=[node],
                                sources=[spec.name.source if spec.name else None],
                            )

    def _check_litera(self) -> None:
        """The specification and its assembly drawing share one litera; a component's litera is not lower than
        the assembly's (GOST 2.103)."""
        tree = self.tree
        for des, items in tree.spec_by_des.items():
            doc, spec = items[0]
            spec_lit = text(spec.litera)
            for ref in tree.drawings_by_des.get(f"{des} {self.sb}", []):
                sb_lit = ref.tb_value("litera")
                if spec_lit and sb_lit and spec_lit != sb_lit:
                    self.add(
                        "B1.LITERA.001",
                        des,
                        f"{des}: литера спецификации «{spec_lit}», сборочного чертежа «{sb_lit}»",
                        expected=spec_lit,
                        actual=str(sb_lit),
                        docs=[doc.id, ref.doc.id],
                        nodes=tree.node_index.get(des, []),
                        sources=[spec.litera.source if spec.litera else None, ref.tb_source("litera")],
                    )
            rank = self.eskd.litera_rank(spec_lit)
            if rank is None:
                continue
            for row in spec.rows:
                child = self.eskd.normalize_designation(text(row.designation))
                if not child or doc_code(child):
                    continue
                child_docs: list[tuple[str | float | int | None, str, SourceRef | None]] = []
                for ref in tree.drawings_by_des.get(child, []):
                    child_docs.append((ref.tb_value("litera"), ref.doc.id, ref.tb_source("litera")))
                for cdoc, cspec in tree.spec_by_des.get(child, []):
                    child_docs.append((text(cspec.litera), cdoc.id, cspec.litera.source if cspec.litera else None))
                for lit, doc_id, src in child_docs:
                    crank = self.eskd.litera_rank(str(lit) if lit else None)
                    if crank is not None and crank < rank:
                        self.add(
                            "B1.LITERA.002",
                            child,
                            f"{child}: литера «{lit}» ниже литеры сборочной единицы {des} «{spec_lit}»",
                            expected=f"не ниже {spec_lit}",
                            actual=str(lit),
                            docs=[doc_id, doc.id],
                            nodes=tree.node_index.get(child, []),
                            sources=[src, row.source],
                        )


def check(tree: ProductTree, eskd: Designations, rules: dict[str, ActiveRule]) -> list[Discrepancy]:
    """Discrepancies of the kit; the tree nodes they concern get their ids and the ``warning`` status."""
    return Checker(tree, eskd, rules).run()
