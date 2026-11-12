"""Block 1: completeness checks over the product tree.

Input: the product tree of :mod:`kdcheck.tree.product` and its indexes. Output: the discrepancies, each found by a
rule of :mod:`kdcheck.checks.rules`, classified by type and linked to the tree nodes and to the documents and page
regions they come from. A rule turned off in ``configs/checks.yaml`` reports nothing.

A finding that a document is missing, or that one is in no specification, is preliminary when the kit holds documents
the core did not read and the one wanted may be among them (ADR-0027): files it could not read or tell, for a missing
drawing, document or specification; specifications without rows read, for one in no specification. The finding names
them and stays in the report.
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict

from kdcheck.checks.rules import ActiveRule
from kdcheck.config import Checks
from kdcheck.normalize.designations import Designations, base_designation, doc_code, split_variant
from kdcheck.schemas import Discrepancy, DocumentInfo, SourceRef, Specification, SpecRow, TreeNode
from kdcheck.tree.product import ProductTree, text

_FORMAT = re.compile(r"А\d(?:Х\d{1,2})?")  # a format of GOST 2.301 as a specification writes it: «А4», «А4х3»
_COMPONENTS = ("assembly", "part", "standard", "other_item", "material")  # sections whose rows have a quantity
_DESIGNATED = ("assembly", "part")  # sections whose positions have a designation; documents get no position
UNREAD_DOCUMENTS = "в комплекте есть документы, вид или обозначение которых не прочитаны"
UNREAD_SPECIFICATIONS = "в комплекте есть спецификации без прочитанных строк"


class Checker:
    def __init__(
        self,
        tree: ProductTree,
        eskd: Designations,
        rules: dict[str, ActiveRule],
        checks: Checks,
    ) -> None:
        self.tree = tree
        self.eskd = eskd
        self.rules = rules
        self.names = checks.names
        self.sheets = checks.sheets
        self.positions = checks.positions
        self.listed = checks.preliminary.listed
        self.sb = eskd.words.assembly_drawing_code
        self.sections = eskd.words.spec_sections
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
        preliminary: bool = False,
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
            preliminary=preliminary,
        )
        for n in nodes or []:
            n.discrepancy_ids.append(disc.id)
            if n.status == "ok":
                n.status = "warning"
        self.discrepancies.append(disc)

    def run(self) -> list[Discrepancy]:
        self._check_missing()
        self._check_unlisted_assemblies()
        self._check_orphans()
        self._check_duplicates()
        self._check_names()
        self._check_litera()
        self._check_sheets()
        self._check_sections()
        self._check_formats()
        self._check_positions()
        self._check_first_use()
        return self.discrepancies

    def _unread_note(self, docs: list[DocumentInfo], what: str, also: str | None = None) -> str:
        """The tail of a preliminary finding: ``also``, a reason of its own, and which documents of the kit were not
        read."""
        reasons = [also] if also else []
        if docs:
            n = self.listed
            listed = ", ".join(d.path for d in docs[:n]) + (f" и ещё {len(docs) - n}" if len(docs) > n else "")
            reasons.append(f"{what} ({len(docs)}: {listed}), искомый документ может быть среди них")
        return f". Находка предварительная: {'; '.join(reasons)}" if reasons else ""

    def _same_name(self, a: str | None, b: str | None, *sources: SourceRef | None) -> bool:
        """Whether two names are one: the same after normalization or, when one of them was read by OCR or a model,
        close enough for the difference to be misread letters.

        A misread «сборочный чертеж» on an assembly drawing is no part of the name. Names of as many words are
        compared word by word, so a misread letter is forgiven and another word, as «цилиндр» against
        «гидроцилиндр», is not; names of a different number of words, as when OCR loses a space, are compared
        whole."""
        first, second = self.eskd.normalize_name(a), self.eskd.normalize_name(b)
        if first == second:
            return True
        if not any(s is not None and s.method in self.names.methods for s in sources):
            return False
        first, second = self._without_misread_document_name(first), self._without_misread_document_name(second)
        words, others = first.split(), second.split()
        if len(words) == len(others):
            return all(self._close(w, o) for w, o in zip(words, others, strict=True))
        return self._close(first, second)

    def _close(self, a: str, b: str) -> bool:
        return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= self.names.min_similarity

    def _without_misread_document_name(self, name: str) -> str:
        """``name`` without a trailing «сборочный чертеж» read with misread letters, as «сдорочный чертеж»;
        :meth:`Designations.normalize_name` removes the words read right."""
        tail = self.eskd.words.assembly_drawing_name.split()
        words = name.split()
        if len(words) > len(tail) and self._close(" ".join(words[-len(tail) :]), " ".join(tail)):
            return " ".join(words[: -len(tail)])
        return name

    def _check_missing(self) -> None:
        """Positions whose document is not in the kit, in the order of the tree: one discrepancy a row, with every
        node of it. A row of a group specification stands under each variant it serves, and a row of an assembly used
        twice under each use."""
        by_row: dict[int, list[TreeNode]] = {}
        for node in self.tree.nodes():
            if node.status == "missing":
                row = self.tree.rows.get(node.id)
                by_row.setdefault(id(row) if row is not None else id(node), []).append(node)
        for nodes in by_row.values():
            node = nodes[0]
            row = self.tree.rows.get(node.id)
            unread = self.tree.unread
            tail = self._unread_note(unread, UNREAD_DOCUMENTS) if unread else ""
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
                    )
                    + tail,
                    docs=node.drawing_document_ids + [d.id for d in unread],
                    nodes=nodes,
                    sources=[row.source if row else None],
                    preliminary=bool(unread),
                )
                continue
            parent = self.tree.parents[node.id]
            document = node.kind == "document"
            what = "Документ" if document else "Чертёж детали"
            why, doubted = self.tree.doubtful.get(node.id, (None, []))
            tail = self._unread_note(unread, UNREAD_DOCUMENTS, why)
            self.add(
                "B1.MISSING.002" if document else "B1.MISSING.001",
                node.designation,
                f"{what} {node.designation} «{node.name or '—'}» указан в спецификации {parent.designation}, "
                "но в комплекте не найден" + tail,
                expected=node.designation,
                docs=([parent.spec_document_id] if parent.spec_document_id else []) + doubted + [d.id for d in unread],
                nodes=nodes,
                sources=[row.source if row else None],
                preliminary=bool(unread) or why is not None,
            )

    def _check_unlisted_assemblies(self) -> None:
        """An assembly drawing of an assembly without a specification that no specification names either, as in a kit
        without specifications: GOST 2.102 makes the specification the main document of an assembly. A listed one is
        reported from its row."""
        tree = self.tree
        unread = tree.unread
        tail = self._unread_note(unread, UNREAD_DOCUMENTS) if unread else ""
        for des, refs in tree.drawings_by_des.items():
            base = base_designation(des)
            if doc_code(des) != self.sb or base in tree.referenced:
                continue
            if base in tree.spec_by_des or split_variant(base)[0] in tree.spec_by_des:
                continue
            for ref in refs:
                self.add(
                    "B1.SPEC.001",
                    base,
                    f"Сборочный чертёж {des} ({ref.doc.path}) есть, но спецификации сборочной единицы {base} "
                    "в комплекте нет" + tail,
                    actual=ref.doc.path,
                    docs=[ref.doc.id] + [d.id for d in unread],
                    sources=[ref.tb_source("designation")],
                    preliminary=bool(unread),
                )

    def _check_orphans(self) -> None:
        tree = self.tree
        if not tree.specs:
            return
        unread = [doc for doc, spec in tree.specs if not spec.rows]  # specifications whose rows were not read
        tail = self._unread_note(unread, UNREAD_SPECIFICATIONS) if unread else ""
        for des, refs in tree.drawings_by_des.items():
            base, code = base_designation(des), doc_code(des)
            if des in tree.referenced or (code == self.sb and base in tree.referenced):
                continue
            for ref in refs:
                self.add(
                    "B1.ORPHAN.001",
                    des,
                    f"Документ {des} «{ref.doc.name or '—'}» ({ref.doc.path}) есть в комплекте, "
                    "но не указан ни в одной спецификации" + tail,
                    actual=ref.doc.path,
                    docs=[ref.doc.id] + [d.id for d in unread],
                    sources=[ref.tb_source("designation")],
                    preliminary=bool(unread),
                )

    def _check_duplicates(self) -> None:
        tree = self.tree
        for des, refs in tree.drawings_by_des.items():
            if len(refs) < 2:
                continue
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
        names: dict[str, list[list[tuple[DocumentInfo, SpecRow]]]] = defaultdict(list)  # designation → rows by name
        for doc, spec in tree.specs:
            for r in spec.rows:
                row_des = self.eskd.normalize_designation(text(r.designation))
                if not row_des:
                    continue
                for rows in names[row_des]:
                    first = rows[0][1]
                    if self._same_name(text(first.name), text(r.name), _name_source(first), _name_source(r)):
                        rows.append((doc, r))
                        break
                else:
                    names[row_des].append([(doc, r)])
        for des, by_name in names.items():
            if len(by_name) > 1:
                variants = [f"«{text(rows[0][1].name)}» ({rows[0][0].path})" for rows in by_name]
                self.add(
                    "B1.DUP.002",
                    des,
                    f"Обозначение {des} указано в спецификациях с разными наименованиями: " + ", ".join(variants),
                    docs=[d.id for rows in by_name for d, _ in rows],
                    nodes=tree.node_index.get(des, []),
                    sources=[r.source for rows in by_name for _, r in rows],
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
        for des, items in tree.spec_by_des.items():
            drawings = [r for r in tree.drawings_by_des.get(des, []) if r.doc.kind == "part_drawing"]
            if not drawings:  # the assembly drawing of an assembly is «… СБ»
                continue
            specs = ", ".join(d.path for d, _ in items)
            files = ", ".join(r.doc.path for r in drawings)
            self.add(
                "B1.DUP.004",
                des,
                f"Обозначение {des} присвоено спецификации сборочной единицы ({specs}) и чертежу детали "
                f"«{drawings[0].doc.name or '—'}» ({files}): документ одного изделия поглощает другой",
                actual=files,
                docs=[d.id for d, _ in items] + [r.doc.id for r in drawings],
                nodes=tree.node_index.get(des, []),
                sources=[s.designation.source if s.designation else None for _, s in items]
                + [r.tb_source("designation") for r in drawings],
            )

    def _check_sheets(self) -> None:
        """A document whose title block counts more sheets than the kit has, or whose sheets skip a number."""
        for doc in self.tree.documents:
            if doc.apart or not doc.found:
                continue
            count = doc.sheets
            read = count.source.method if count is not None and count.source is not None else None
            trusted = count is not None and read in self.sheets.count_methods
            counted = count.value if count is not None and trusted and isinstance(count.value, int) else None
            last = max(counted or 0, *doc.found)
            gone = [n for n in range(1, last + 1) if n not in doc.found]
            if not gone:
                continue
            listed = ", ".join(map(str, gone))
            have = ", ".join(map(str, doc.found))
            told = f"в основной надписи листов {counted}, " if counted is not None else ""
            source = count.source if counted is not None and count is not None else None
            files = ", ".join(f.path for f in doc.files)
            self.add(
                "B1.SHEETS.001",
                doc.designation,
                f"Документ {doc.designation} ({files}): {told}в комплекте листы {have}; нет листов {listed}",
                expected=str(last),
                actual=have,
                docs=[f.id for f in doc.files],
                nodes=self.tree.node_index.get(doc.designation, []),
                sources=[source],
            )

    def _check_names(self) -> None:
        tree = self.tree
        for des, nodes in tree.node_index.items():
            for node in nodes:
                expected = node.name
                if not expected or node.kind == "document":
                    continue
                row = tree.rows.get(node.id)
                listed = _name_source(row) if row is not None else None
                candidates = list(tree.drawings_by_des.get(des, []))
                if node.kind == "assembly":
                    candidates = list(tree.drawings_by_des.get(f"{des} {self.sb}", []))
                for ref in candidates:
                    actual = ref.tb_value("name") or ref.doc.name
                    if not actual or self._same_name(str(actual), expected, listed, ref.tb_source("name")):
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
                        source = spec.name.source if spec.name else None
                        if own and not self._same_name(own, expected, listed, source):
                            self.add(
                                "B1.NAME.002",
                                des,
                                f"{des}: в вышестоящей спецификации «{expected}», в собственной спецификации «{own}»",
                                expected=expected,
                                actual=own,
                                docs=[node.spec_document_id],
                                nodes=[node],
                                sources=[source],
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

    def _check_sections(self) -> None:
        """An assembly written in «Детали», or a part in «Сборочные единицы» (GOST 2.106, 3.5): the tree takes each
        for what its documents are, and the row keeps its section."""
        for node in self.tree.nodes():
            listed = self.sections.get(node.section or "")
            if node.kind not in ("assembly", "part") or listed not in ("assembly", "part") or listed == node.kind:
                continue
            parent = self.tree.parents.get(node.id)
            row = self.tree.rows.get(node.id)
            what, why = (
                ("сборочная единица", "в комплекте есть её спецификация")
                if node.kind == "assembly"
                else ("деталь", "в комплекте есть её чертёж детали, а спецификации и сборочного чертежа нет")
            )
            where = parent.designation if parent is not None else "—"
            self.add(
                "B1.SECTION.001",
                node.designation,
                f"{node.designation} «{node.name or '—'}» записана в спецификации {where} в разделе «{node.section}», "
                f"но это {what}: {why}",
                expected="Сборочные единицы" if node.kind == "assembly" else "Детали",
                actual=node.section,
                docs=[d for d in [parent.spec_document_id if parent else None, node.spec_document_id] if d]
                + node.drawing_document_ids,
                nodes=[node],
                sources=[row.source if row else None],
            )

    def _format(self, value: str | None) -> str | None:
        """A format of GOST 2.301 as a specification writes it, «А4», «А4х3»; None for «БЧ», «*» or any other text."""
        cell = self.eskd.cyrillic((value or "").upper()).replace(" ", "")
        return cell.replace("Х", "х") if _FORMAT.fullmatch(cell) else None

    def _sheet_formats(self) -> dict[str, tuple[DocumentInfo, list[str]]]:
        """Document id → the document and the formats of its sheets in every file of it."""
        found: dict[str, tuple[DocumentInfo, list[str]]] = {}
        for doc in self.tree.documents:
            formats = [f for info in doc.files for p in info.pages if not p.problem and (f := self._format(p.format))]
            for info in doc.files:
                found[info.id] = (doc.first, formats)
        loose = [d.doc for d in self.tree.drawings] + [d for d, _ in self.tree.specs]
        for info in loose:
            if info.id not in found:
                found[info.id] = (info, [f for p in info.pages if not p.problem and (f := self._format(p.format))])
        return found

    def _check_formats(self) -> None:
        """The format column of a row names the format of the document its designation is (GOST 2.106, 3.6): the
        drawing of a part or a document, the specification of an assembly. Some makers write the format of the
        assembly drawing for an assembly, so either one is taken. A document on sheets of several formats is written
        «*» with the formats in the note, and is not checked."""
        formats = self._sheet_formats()
        seen: set[tuple[int, str]] = set()
        for node in self.tree.nodes():
            row = self.tree.rows.get(node.id)
            written = self._format(text(row.format)) if row is not None and row.format is not None else None
            if row is None or row.format is None or written is None:
                continue
            ids = node.drawing_document_ids
            if node.kind == "assembly":  # its specification, then its assembly drawing; without the first, unknown
                ids = [node.spec_document_id, *node.drawing_document_ids] if node.spec_document_id else []
            known = [formats[d] for d in ids if d in formats and formats[d][1]]
            if not known or (id(row), ids[0]) in seen or any(written in actual for _, actual in known):
                continue
            seen.add((id(row), ids[0]))
            doc, actual = known[0]
            parent = self.tree.parents.get(node.id)
            where = parent.designation if parent is not None else "—"
            listed = ", ".join(sorted(set(actual)))
            self.add(
                "B1.FORMAT.001",
                node.designation,
                f"{node.designation}: в спецификации {where} формат {written}, "
                f"документ {doc.path} выполнен на {listed}",
                expected=written,
                actual=listed,
                docs=[d for d in [parent.spec_document_id if parent else None, doc.id] if d],
                nodes=[node],
                sources=[row.format.source],
            )

    def _checked(self, row: SpecRow) -> bool:
        return row.source is not None and row.source.method in self.positions.methods

    def _check_positions(self) -> None:
        """Within a specification, one position number on rows that serve one variant, a component row without a
        quantity and a position of an assembly or a part without a designation (GOST 2.106, 3.8 and 3.11); the
        documents of «Документация» get no position numbers.
        A gap in the numbers is no anomaly: GOST lets positions be reserved. Rows of the variable data of different
        variants by variant A share positions, and rows of different variants by variant B may share one."""
        for doc, spec in self.tree.specs:
            des = text(spec.designation) or doc.designation
            by_position: dict[tuple[str | None, str], list[SpecRow]] = defaultdict(list)
            for r in spec.rows:
                kind = self.sections.get(r.section or "")
                if not self._checked(r):
                    continue
                if kind in _DESIGNATED and r.designation is None and r.name is not None:
                    self._no_designation(doc, des, r)
                if kind not in _COMPONENTS:
                    continue
                if r.quantity is None and not r.quantities:
                    self._no_quantity(doc, des, r)
                position = text(r.position)
                if position and position.isdigit():
                    by_position[(r.variant, position)].append(r)
            for (_, position), rows in by_position.items():
                clash = [r for r in rows if any(_same_variants(r, o) for o in rows if o is not r)]
                if len(clash) < 2:
                    continue
                named = "; ".join(f"{text(r.designation) or '—'} «{text(r.name) or '—'}»" for r in clash)
                self.add(
                    "B1.POS.001",
                    des,
                    f"В спецификации {des} ({doc.path}) позиция {position} у {len(clash)} строк: {named}",
                    actual=named,
                    docs=[doc.id],
                    sources=[r.position.source if r.position else r.source for r in clash],
                )

    def _no_quantity(self, doc: DocumentInfo, des: str | None, row: SpecRow) -> None:
        item = text(row.designation) or text(row.name) or "—"
        self.add(
            "B1.POS.002",
            des,
            f"В спецификации {des} ({doc.path}) у позиции {text(row.position) or '—'} {item} "
            f"раздела «{row.section}» нет количества",
            expected="количество",
            docs=[doc.id],
            sources=[row.source],
        )

    def _no_designation(self, doc: DocumentInfo, des: str | None, row: SpecRow) -> None:
        self.add(
            "B1.POS.003",
            des,
            f"В спецификации {des} ({doc.path}) у позиции {text(row.position) or '—'} «{text(row.name) or '—'}» "
            f"раздела «{row.section}» нет обозначения: документ позиции в комплекте не найти",
            expected="обозначение",
            docs=[doc.id],
            sources=[row.source],
        )

    def _first_uses(self, node: TreeNode) -> list[tuple[str, str, SourceRef | None]]:
        """«Перв. примен.» of the documents of a node, with the document and the source of each."""
        if node.kind == "assembly":
            specs: list[Specification] = [s for d, s in self.tree.specs if d.id == node.spec_document_id]
            return [
                (str(s.first_use.value), node.spec_document_id or "", s.first_use.source)
                for s in specs
                if s.first_use is not None and s.first_use.value not in (None, "")
            ]
        found = []
        for ref in self.tree.drawings_by_des.get(node.designation or "", []):
            value = ref.tb_value("first_use")
            if value not in (None, ""):
                found.append((str(value), ref.doc.id, ref.tb_source("first_use")))
        return found

    def _lists(self, spec: Specification, item: str) -> bool:
        """Whether a specification lists ``item``, or a variant of it."""
        wanted = split_variant(item)[0]
        listed = (self.eskd.normalize_designation(text(r.designation)) for r in spec.rows)
        return any(d is not None and split_variant(d)[0] == wanted for d in listed)

    def _check_first_use(self) -> None:
        """«Перв. примен.» names the specification an item was first written in (GOST 2.104). Informational: an item
        first used in another product names one outside the kit, and a specification without rows read tells
        nothing, nor does one read by OCR, which may miss a row; so only a specification of the kit whose rows,
        read in a way of ``positions.methods``, do not list the item is reported. The specification
        and the assembly drawing of an assembly name the product it goes into, as GOST has them do: for them the
        item is the assembly; a drawing that names its own assembly, which lists it, is taken too."""
        tree = self.tree
        for des, nodes in tree.node_index.items():
            node = nodes[0]
            parent = tree.parents.get(node.id)
            own = node.kind == "document" and parent is not None and base_designation(des) == parent.designation
            item = parent.designation if own and parent is not None and parent.designation else base_designation(des)
            done: set[str] = set()
            for value, doc_id, source in self._first_uses(node):
                named = self.eskd.normalize_designation(value)
                first = split_variant(named)[0] if named else None
                if first is None or doc_id in done or first == split_variant(item)[0]:
                    continue
                if own and first == split_variant(base_designation(des))[0]:
                    continue
                specs = tree.spec_by_des.get(first, [])
                rows = specs[0][1].rows if specs else []
                if not rows or not all(self._checked(r) for r in rows) or self._lists(specs[0][1], item):
                    continue
                done.add(doc_id)
                self.add(
                    "B1.FIRSTUSE.001",
                    des,
                    f"{des}: «Перв. примен.» — {named}, но в спецификации {named} ({specs[0][0].path}) "
                    f"{item} не записан",
                    expected=item,
                    actual=named,
                    docs=[doc_id, specs[0][0].id],
                    nodes=nodes,
                    sources=[source],
                )


def _same_variants(a: SpecRow, b: SpecRow) -> bool:
    """Whether two rows of a group specification by variant B serve a variant in common; any row of another one."""
    if not a.quantities or not b.quantities:
        return True
    return bool(set(a.quantities) & set(b.quantities))


def _name_source(row: SpecRow) -> SourceRef | None:
    return row.name.source if row.name is not None else None


def check(tree: ProductTree, eskd: Designations, rules: dict[str, ActiveRule], checks: Checks) -> list[Discrepancy]:
    """Discrepancies of the kit; the tree nodes they concern get their ids and the ``warning`` status."""
    return Checker(tree, eskd, rules, checks).run()
