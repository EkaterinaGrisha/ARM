"""Baseline pipeline: rules over the PDF text layer and, when available, OCR.

A reference implementation and an integration example: any stage can be replaced by a model (VLM, LLM, another OCR)
while the report format stays the same. Thresholds, templates and dictionaries come from :class:`Settings`; the
report records their hash and the engines with versions. With ``vlm`` on, a vision-language model reads the title
block cells of scans that the rules left empty (ADR-0014).
"""

from __future__ import annotations

import itertools
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal, TypeVar

from kdcheck.checks import rules
from kdcheck.checks.completeness import check
from kdcheck.config import Settings, settings_sha256
from kdcheck.extract import review, specification, tech_requirements, title_block
from kdcheck.ingest.document import LoadedDocument, Page, kit_files, load_document, page_raster
from kdcheck.ingest.pdf import get_backend
from kdcheck.ingest.text import Line
from kdcheck.normalize import file_designation
from kdcheck.normalize.designations import Designations, doc_code
from kdcheck.ocr import cells, tesseract
from kdcheck.params import critical, parameters
from kdcheck.schemas import (
    CriticalParameter,
    Discrepancy,
    DocumentInfo,
    EngineInfo,
    Extracted,
    KitInfo,
    LogEntry,
    Parameter,
    PartRecord,
    Report,
    SourceRef,
    Specification,
    Summary,
    TitleBlock,
    TreeNode,
)
from kdcheck.tree.documents import DRAWINGS, Assembly, assemble
from kdcheck.tree.product import DrawingRef, build_tree
from kdcheck.tree.rows import check_rows
from kdcheck.vlm import spec_rows as vlm_spec_rows
from kdcheck.vlm import tech_requirements as vlm_tech_requirements
from kdcheck.vlm import title_block as vlm_title_block
from kdcheck.vlm.client import VlmClient, VlmError, load_prompt

log = logging.getLogger(__name__)
T = TypeVar("T")

# (stage, share done 0..1, note)
ProgressCallback = Callable[[str, float, str | None], None]


def no_progress(stage: str, progress: float, message: str | None = None) -> None:
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class BaselinePipeline:
    name = "baseline"
    version = "0.1.0"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pdf = get_backend(settings.models.pdf.backend)
        self.designations = Designations(settings.dictionaries)  # every grammar; a run narrows them to its kit
        self.eskd = self.designations
        self.rules = rules.configured(settings.checks)
        self.parameters = parameters.Parser(settings.dictionaries, settings.tolerances)
        vlm = settings.models.vlm
        self.vlm = VlmClient(vlm, load_prompt(vlm.prompt)) if vlm.enabled else None
        # the cells of specification rows, with a prompt of their own (ADR-0021)
        self.vlm_rows = VlmClient(vlm, load_prompt(vlm.rows.prompt)) if vlm.enabled else None
        # the technical requirements block, with a prompt and a longer answer of its own (ADR-0023)
        longer = vlm.model_copy(update={"max_tokens": vlm.requirements.max_tokens})
        self.vlm_requirements = VlmClient(longer, load_prompt(vlm.requirements.prompt)) if vlm.enabled else None
        self.vlm_problem: str | None = None  # why the model is not asked in this run
        self.vlm_used = False
        self.later_sheets: set[str] = set()  # files whose form 2a says they are later sheets of a document

    # ------------------------------------------------------------------ run
    def run(self, input_dir: Path, *, kit_id: str, kit_name: str, progress: ProgressCallback = no_progress) -> Report:
        started = time.monotonic()
        report_log: list[LogEntry] = []
        self.vlm_problem = self.vlm.unavailable() if self.vlm is not None else None
        self.vlm_used = False
        self.later_sheets = set()
        files, ignored = kit_files(input_dir, self.settings.ingest.file_names)
        if ignored:
            n = self.settings.checks.preliminary.listed
            listed = ", ".join(p.relative_to(input_dir).as_posix() for p in ignored[:n])
            listed += f" и ещё {len(ignored) - n}" if len(ignored) > n else ""
            message = f"Служебные файлы не входят в комплект ({len(ignored)}): {listed}"
            report_log.append(LogEntry(level="info", message=message))
        self.eskd = self.designations.for_kit([p.stem for p in files])
        report_log += self._designation_systems(files)
        progress("Чтение файлов", 0.0, f"Файлов в комплекте: {len(files)}")
        docs: list[LoadedDocument] = []
        known: dict[str, str] = {}  # SHA-256 → the first file with these bytes
        for i, path in enumerate(files):
            progress("Чтение файлов", 0.6 * i / max(1, len(files)), path.relative_to(input_dir).as_posix())
            doc = load_document(path, input_dir, self.settings, pdf_backend=self.pdf, known=known)
            if doc.info.sha256:
                known.setdefault(doc.info.sha256, doc.info.path)
            docs.append(doc)

        progress("Разбор документов", 0.6, None)
        specs: list[tuple[LoadedDocument, Specification]] = []
        parts: list[PartRecord] = []
        for i, doc in enumerate(docs):
            progress("Разбор документов", 0.6 + 0.25 * i / max(1, len(docs)), doc.info.path)
            if doc.info.status != "ok":
                continue
            try:
                self._classify_and_parse(doc, specs, parts)
            except Exception as exc:  # a parse error in one file never stops the doc set
                log.exception("Ошибка разбора %s", doc.info.path)
                doc.info.status = "warning"
                doc.info.problem = f"Ошибка разбора: {exc}"
                report_log.append(LogEntry(level="error", message=f"Ошибка разбора: {exc}", document_id=doc.id))
        for doc in docs:
            report_log += _page_problems(doc)
        if self.vlm is not None and self.vlm_problem:
            message = f"Модель {self.vlm.engine.model} не ответила: {self.vlm_problem}. "
            message += "Основные надписи сканов прочитаны без неё" if not self.vlm_used else "Часть ячеек она не прочла"
            report_log.append(LogEntry(level="warning", message=message))

        # each stage past parsing is isolated as a file is: a stage that fails leaves its part of the report empty
        # and says so in the log, and the report is still written with what the other stages found
        progress("Проверка комплектности", 0.87, None)
        infos = [d.info for d in docs]
        loose = Assembly(specs=[(d.info, s) for d, s in specs], parts=parts, documents=[], log=[])
        assembly = _stage(
            "сборка документов из листов",
            report_log,
            lambda: assemble(infos, [(d.info, s) for d, s in specs], parts, self.later_sheets),
            loose,
        )
        report_log += [LogEntry(level="warning", message=m) for m in assembly.log]
        kept = {info.id for info, _ in assembly.specs}
        specs, parts = [(d, s) for d, s in specs if d.id in kept], assembly.parts
        designations = {d.info.designation for d in docs if d.info.designation}
        designations |= {f for d in docs if (f := self._filename_designation(d.info.file_name))}
        row_rules = self.settings.dictionaries.row_designations
        if changed := _stage(
            "сверка строк спецификаций с комплектом",
            report_log,
            lambda: check_rows(assembly.specs, designations, self.eskd, row_rules),
            None,
        ):
            report_log.append(
                LogEntry(level="info", message=f"Обозначения строк спецификаций сверены с комплектом: {changed}")
            )
        # the documents a position of a specification may name: drawings, and text documents as «… ПЗ»
        drawing_refs = [
            DrawingRef(doc=d.info, part=next((p for p in parts if p.document_id == d.id), None))
            for d in docs
            if d.info.kind in (*DRAWINGS, "other") and d.info.designation and d.info.sheet_of is None
        ]
        unread = [d.info for d in docs if _unread(d.info)]
        tree = _stage(
            "дерево изделия",
            report_log,
            lambda: build_tree([(d.info, s) for d, s in specs], drawing_refs, self.eskd, assembly.documents, unread),
            None,
        )
        discrepancies: list[Discrepancy] = []
        if tree is not None:
            discrepancies = _stage(
                "правила Блока 1", report_log, lambda: check(tree, self.eskd, self.rules, self.settings.checks), []
            )
            report_log += [LogEntry(level="warning", message=m) for m in tree.log]

        progress("Анализ требований", 0.93, None)
        criticals: list[CriticalParameter] = _stage(
            "отбор критичных параметров", report_log, lambda: critical.select(parts, self.settings.criticality), []
        )

        progress("Формирование отчёта", 0.97, None)

        def mark() -> None:
            for model in [*parts, *(s for _, s in specs)]:
                review.mark(model, self.settings.ingest.review)

        _stage("отметка значений для проверки", report_log, mark, None)
        for d in infos:
            if d.status in ("damaged", "encrypted", "unsupported", "unreadable", "blank", "foreign", "duplicate_file"):
                report_log.append(LogEntry(level="warning", message=d.problem or d.status, document_id=d.id))
        roots = tree.roots if tree is not None else []
        tree_items = _count_nodes(roots)
        need = _count_nodes(
            roots, lambda n: n.status in ("ok", "missing", "warning") and n.kind in ("part", "document")
        )
        have = _count_nodes(roots, lambda n: n.kind in ("part", "document") and bool(n.drawing_document_ids))
        summary = Summary(
            documents_total=len(infos),
            documents_by_status=dict(Counter(d.status for d in infos)),
            documents_by_kind=dict(Counter(d.kind for d in infos)),
            specifications=len(specs),
            drawings=sum(1 for d in drawing_refs if d.doc.kind in DRAWINGS),
            tree_items=tree_items,
            parts=len(parts),
            parameters=sum(len(p.parameters) for p in parts),
            discrepancies_total=len(discrepancies),
            discrepancies_by_type=dict(Counter(x.type for x in discrepancies)),
            critical_parameters=len(criticals),
            completeness_ratio=round(have / need, 4) if need else None,
        )
        kit = KitInfo(
            id=kit_id,
            name=kit_name,
            root_designation=tree.root_designation if tree is not None else None,
            root_name=tree.root_name if tree is not None else None,
            processed_at=_now(),
            duration_sec=round(time.monotonic() - started, 2),
            pipeline_name=self.name,
            pipeline_version=self.version,
            engines=self.engines(),
            config_sha256=settings_sha256(self.settings),
        )
        progress("Готово", 1.0, None)
        return Report(
            kit=kit,
            summary=summary,
            documents=infos,
            specifications=[s for _, s in specs],
            tree=roots,
            discrepancies=discrepancies,
            parts=parts,
            critical_parameters=criticals,
            log=report_log,
        )

    def engines(self) -> list[EngineInfo]:
        """The engines and libraries whose versions the results depend on."""
        found = [EngineInfo(name=name, version=v) for name, v in self.pdf.versions().items()]
        ocr = self.settings.models.ocr
        if ocr.enabled and tesseract.available(ocr.languages):
            found.append(EngineInfo(name=ocr.engine, version=tesseract.version()))
            if (reader := cells.ppocr_reader(self.settings)) is not None:  # the cells of scans (ADR-0024)
                found.append(EngineInfo(name=reader.name, version=reader.version))
        if self.vlm is not None and self.vlm_used:
            found.append(EngineInfo(name=self.vlm.engine.model, version=self.vlm.version))
        if self.vlm_rows is not None and self.vlm_rows.calls:
            found.append(EngineInfo(name=self.vlm_rows.engine.model, version=self.vlm_rows.version))
        if self.vlm_requirements is not None and self.vlm_requirements.calls:
            found.append(EngineInfo(name=self.vlm_requirements.engine.model, version=self.vlm_requirements.version))
        found.append(EngineInfo(name="pillow", version=metadata.version("pillow")))
        return found

    # ------------------------------------------------------------------ classification
    def _classify_and_parse(
        self, doc: LoadedDocument, specs: list[tuple[LoadedDocument, Specification]], parts: list[PartRecord]
    ) -> None:
        info, forms = doc.info, self.settings.forms
        text_pages = [p for p in doc.pages if p.lines]
        if not text_pages:
            self._scan_without_text(doc, parts)
            return
        if any(specification.is_specification(p, forms) for p in text_pages[: forms.specification.header_pages]):
            spec = specification.parse(doc, forms, self.eskd)
            self._read_spec_cells(doc, spec)
            info.kind = "specification"
            info.designation = self._designation(doc, spec.designation) or self._filename_designation(info.file_name)
            info.name = _str(spec.name)
            head = title_block.locate(text_pages[0], forms)
            later = title_block.later_sheet(doc, text_pages[0], forms, self.eskd) if head is None else None
            if later is not None:
                self._set_sheet(doc, later.sheet, "later")
            else:
                self._set_sheet(doc, None, "first" if head is not None and head.form == 2 else None)
            if self._text_document(doc, spec):
                return
            if not spec.rows:
                info.status, info.problem = "warning", "Спецификация распознана, но строки таблицы не найдены"
            specs.append((doc, spec))
            return
        page = text_pages[0]
        geom = title_block.locate(page, forms)
        if geom is not None and geom.form == 2:
            spec = specification.parse(doc, forms, self.eskd)
            self._read_spec_cells(doc, spec)
            info.kind = "specification"
            info.designation = self._designation(doc, spec.designation) or self._filename_designation(info.file_name)
            info.name = _str(spec.name)
            self._set_sheet(doc, None, "first")
            if not self._text_document(doc, spec):
                specs.append((doc, spec))
            return
        if geom is None and (later := title_block.later_sheet(doc, page, forms, self.eskd)) is not None:
            if (record := self._parse_later_sheet(doc, page, later)) is not None:
                parts.append(record)
            return
        if geom is None:
            fallback = title_block.default_geometry(page, forms)
            template = forms.title_block
            wide = title_block.lines_in(page, fallback.rect(template.designation_overflow), template.line_row_height)
            has_designation = any(self.eskd.find_designations(ln.text) for ln in wide)
            if page.method == "ocr" and has_designation:
                geom = fallback
            else:
                if page.method == "ocr" and doc.unreadable:
                    info.status, info.problem = "unreadable", f"Нечитаемый скан: {doc.unreadable}"
                elif page.method == "ocr":
                    info.status = "warning"
                    info.problem = "Скан распознан, но основная надпись не найдена — тип документа не определён"
                else:
                    info.status = "foreign"
                    info.problem = "Посторонний документ: нет основной надписи чертежа и граф спецификации"
                return
        parts.append(self._parse_drawing(doc, page, geom))

    def _parse_drawing(self, doc: LoadedDocument, page: Page, geom: title_block.Geometry) -> PartRecord:
        info, forms = doc.info, self.settings.forms
        tb = title_block.parse(doc, page, geom, forms, self.eskd)
        if self.vlm is not None and self.vlm_problem is None and page.method in self.settings.models.vlm.methods:
            self._read_cells(doc, page, geom, tb)
        self._designation_from_filename(doc, tb)
        self._set_sheet(doc, tb.sheet, "first" if geom.anchored else None)
        designation = self._designation(doc, tb.designation)
        name = _str(tb.name)
        info.kind = "assembly_drawing" if self.eskd.is_assembly_drawing(designation, name) else "part_drawing"
        info.designation = designation
        info.name = name
        if page.method == "ocr":
            info.notes.append("Текст получен распознаванием скана (OCR) — значения требуют проверки")
        part = PartRecord(document_id=doc.id, designation=designation, name=name, kind=info.kind, title_block=tb)

        counter = itertools.count(1)

        def next_id() -> str:
            return f"{doc.id}-p{next(counter)}"

        block = forms.tech_requirements
        tt_lines = tech_requirements.requirement_lines(page, geom, block)
        part.requirements = tech_requirements.requirement_items(doc, page, tt_lines, block)
        if (
            self.vlm_requirements is not None
            and self.vlm_problem is None
            and page.method in self.settings.models.vlm.methods
        ):
            loose = tech_requirements.requirement_lines(page, geom, block, numbered=False)
            if loose and (read := self._read_requirements(doc, page, loose)):  # an empty answer keeps OCR's
                part.requirements, tt_lines = read, loose
        for item in part.requirements:
            part.parameters += self.parameters.requirement(item, next_id)
        skip = {id(ln) for ln in tt_lines}
        edge = forms.title_block.edge_tolerance
        tb_x0, tb_y0 = geom.x0 - edge * geom.s, geom.y0 - edge * geom.s

        def aside(pg: Page, ln: Line) -> bool:  # technical requirements and the title block
            return id(ln) in skip or (pg is page and ln.cx >= tb_x0 and ln.cy >= tb_y0)

        part.parameters += self._annotations(doc, aside, next_id)
        return part

    def _parse_later_sheet(self, doc: LoadedDocument, page: Page, later: title_block.LaterSheet) -> PartRecord | None:
        """A later sheet of a drawing kept in a file of its own (ADR-0017): the designation and the number of the sheet
        of its form 2a, and the parameters drawn on its field. The document gets them at its first sheet. A later
        sheet of a text document, «… ПЗ», is of kind ``other`` and has no parameters."""
        info, forms = doc.info, self.settings.forms
        tb = TitleBlock(designation=later.designation, sheet=later.sheet)
        self._designation_from_filename(doc, tb)
        self._set_sheet(doc, tb.sheet, "later")
        designation = self._designation(doc, tb.designation)
        info.kind = "assembly_drawing" if self.eskd.is_assembly_drawing(designation, None) else "part_drawing"
        info.designation = designation
        info.name = self._filename_name(info.file_name)
        if designation is not None and doc_code(designation) in self.settings.dictionaries.text_codes:
            info.kind, info.title_block = "other", tb
            return None
        if designation is None:
            info.status, info.problem = "warning", "Последующий лист чертежа: обозначение не прочитано"
        if page.method == "ocr":
            info.notes.append("Текст получен распознаванием скана (OCR) — значения требуют проверки")
        part = PartRecord(document_id=doc.id, designation=designation, name=info.name, kind=info.kind, title_block=tb)
        counter = itertools.count(1)

        def next_id() -> str:
            return f"{doc.id}-p{next(counter)}"

        part.parameters += self._annotations(doc, lambda pg, ln: pg is page and later.holds(ln, forms), next_id)
        return part

    def _annotations(
        self, doc: LoadedDocument, aside: Callable[[Page, Line], bool], next_id: Callable[[], str]
    ) -> list[Parameter]:
        """The parameters written on the field of each page, but for the lines ``aside`` keeps out."""
        sheet = self.settings.forms.sheet
        top, right = sheet.general_roughness_zone
        found: list[Parameter] = []
        for pg in doc.pages:
            on_page: list[Parameter] = []
            try:
                for ln in pg.lines:
                    if ln.vertical or aside(pg, ln):
                        continue
                    if ln.bbox[0] < sheet.binding_margin * pg.mm:
                        continue  # binding margin
                    general_zone = ln.cy < top * pg.mm and ln.cx > pg.width - right * pg.mm
                    on_page += self.parameters.annotation(doc, pg, ln, next_id, general_zone=general_zone)
            except Exception as exc:  # a page that fails to parse never costs the parameters of the others
                log.exception("Ошибка разбора страницы %s в %s", pg.number, doc.info.path)
                doc.page_failed(pg, f"ошибка разбора параметров: {exc}")
                continue
            found += on_page
        return found

    def _designation_from_filename(self, doc: LoadedDocument, tb: TitleBlock) -> None:
        """The designation of the file name when the title block gave none."""
        if tb.designation is not None:
            return
        info = doc.info
        fname = self._filename_designation(info.file_name)
        if not fname:
            return
        confidence = self.settings.ingest.confidence.filename
        tb.designation = Extracted(
            value=fname,
            raw=info.file_name,
            confidence=confidence,
            source=SourceRef(document_id=doc.id, page=1, method="filename", text=info.file_name, confidence=confidence),
        )
        info.notes.append("Обозначение в основной надписи не распознано — взято из имени файла")

    def _set_sheet(self, doc: LoadedDocument, read: Extracted | None, form: Literal["first", "later"] | None) -> None:
        """The number of the sheet the file starts with (ADR-0017): its «Лист» cell, else 1 under form 1 or 2 of a
        first sheet, else the number its name gives. A number from the name alone makes the file a later sheet of a
        document only when form 2a confirms it."""
        info = doc.info
        named = self.settings.ingest.file_names.sheet(Path(info.file_name).stem)
        number = read.value if read is not None and isinstance(read.value, int) else None
        if number is None and form == "first":
            number = 1
        if number is not None and named is not None and number != named:
            info.notes.append(f"Номер листа в имени файла ({named}) не совпадает с основной надписью ({number})")
        info.sheet = number if number is not None else named
        if form == "later":
            self.later_sheets.add(doc.id)

    def _text_document(self, doc: LoadedDocument, spec: Specification) -> bool:
        """Whether a document read as a specification is a text document of the kit: its designation has a code, as
        «… ПЗ» or «… ВП» has, and a specification has none (GOST 2.102). It is then of kind ``other``, with its title
        block, and its table is no rows of a specification."""
        info = doc.info
        code = doc_code(info.designation) if info.designation else None
        if code is None:
            return False
        info.kind = "other"
        info.title_block = TitleBlock(
            designation=spec.designation, name=spec.name, litera=spec.litera, sheets=spec.sheets
        )
        info.notes.append(
            f"Документ с кодом «{code}» на бланке формы 2 — не спецификация: строки таблицы не разбираются"
        )
        return True

    def _read_spec_cells(self, doc: LoadedDocument, spec: Specification) -> None:
        """A specification read by the model as a drawing is (ADR-0021): the cells of form 2 on the first page that
        has it, its designation, name and litera, and the cells of the rows of each page whose table was placed by its
        ruled lines."""
        if self.vlm is None or self.vlm_rows is None or self.vlm_problem is not None:
            return
        forms, methods = self.settings.forms, self.settings.models.vlm.methods
        pages = [p for p in doc.pages if p.lines and p.method in methods]
        for page in pages:
            geom = title_block.locate(page, forms)
            if geom is not None and geom.form == 2:
                tb = TitleBlock(designation=spec.designation, name=spec.name, litera=spec.litera)
                self._read_cells(doc, page, geom, tb)
                spec.designation, spec.name, spec.litera = tb.designation, tb.name, tb.litera
                break
        for page in pages:
            if self.vlm_problem is not None or page.regions is None or page.regions.table is None:
                continue
            calls = self.vlm_rows.calls
            image = page_raster(doc, page, self.settings, self.pdf)
            try:
                doc.info.notes += vlm_spec_rows.read_rows(
                    doc, page, spec.rows, client=self.vlm_rows, image=image, settings=self.settings, eskd=self.eskd
                )
            except VlmError as exc:  # the server stopped answering: the other scans are read without the model
                self.vlm_problem = str(exc)
                doc.info.notes.append(f"Модель не прочитала строки спецификации: {exc}")
            self.vlm_used = self.vlm_used or self.vlm_rows.calls > calls

    def _read_requirements(self, doc: LoadedDocument, page: Page, lines: list[Line]) -> list[Extracted] | None:
        """The items of the technical requirements block over ``lines``, read by the model (ADR-0023); None or none
        when it did not read them, so the items of OCR stay."""
        assert self.vlm_requirements is not None
        calls = self.vlm_requirements.calls
        raster = page_raster(doc, page, self.settings, self.pdf)
        try:
            items = vlm_tech_requirements.read_block(
                doc, page, lines, client=self.vlm_requirements, raster=raster, settings=self.settings
            )
        except VlmError as exc:
            if exc.fatal:  # the server stopped answering: the other scans are read without the model
                self.vlm_problem = str(exc)
            doc.info.notes.append(f"Модель не прочитала технические требования: {exc}")
            items = None
        self.vlm_used = self.vlm_used or self.vlm_requirements.calls > calls
        return items

    def _read_cells(self, doc: LoadedDocument, page: Page, geom: title_block.Geometry, tb: TitleBlock) -> None:
        """The title block cells the rules left empty, read by the vision-language model (ADR-0014)."""
        assert self.vlm is not None
        calls = self.vlm.calls
        raster = page_raster(doc, page, self.settings, self.pdf)
        try:
            doc.info.notes += vlm_title_block.read_cells(
                doc, page, geom, tb, client=self.vlm, raster=raster, settings=self.settings, eskd=self.eskd
            )
        except VlmError as exc:  # the server stopped answering: the other scans are read without the model
            self.vlm_problem = str(exc)
            doc.info.notes.append(f"Модель не прочитала основную надпись: {exc}")
        self.vlm_used = self.vlm_used or self.vlm.calls > calls

    def _scan_without_text(self, doc: LoadedDocument, parts: list[PartRecord]) -> None:
        info = doc.info
        if doc.unreadable:
            info.status, info.problem = "unreadable", f"Нечитаемый скан: {doc.unreadable}"
            return
        des = self._filename_designation(info.file_name)
        info.status = "warning"
        if not des:
            info.problem = "Скан без текстового слоя: распознавание (OCR) недоступно, обозначение не определено"
            return
        info.problem = "Скан без текстового слоя: распознавание (OCR) недоступно, обозначение взято из имени файла"
        self._set_sheet(doc, None, None)
        info.designation = des
        info.name = self._filename_name(info.file_name)
        info.kind = "assembly_drawing" if self.eskd.is_assembly_drawing(des, None) else "part_drawing"
        confidence = self.settings.ingest.confidence
        src = SourceRef(
            document_id=doc.id, page=1, method="filename", text=info.file_name, confidence=confidence.filename
        )
        tb = TitleBlock(
            designation=Extracted(value=des, raw=info.file_name, confidence=confidence.filename, source=src)
        )
        if info.name:
            tb.name = Extracted(value=info.name, raw=info.file_name, confidence=confidence.filename_name, source=src)
        parts.append(PartRecord(document_id=doc.id, designation=des, name=info.name, kind=info.kind, title_block=tb))

    def _designation(self, doc: LoadedDocument, read: Extracted | None) -> str | None:
        """The designation of the title block, decided against the designation of the file name (ADR-0022)."""
        named = self._filename_designation(doc.info.file_name)
        if read is not None and read.value not in (None, "") and named:
            rules = self.settings.dictionaries.file_designations
            if note := file_designation.reconcile(read, named, rules):
                doc.info.notes.append(note)
        return _str(read)

    def _filename_designation(self, file_name: str) -> str | None:
        return self.eskd.normalize_designation(Path(file_name).stem, file_name=True)

    def _filename_name(self, file_name: str) -> str | None:
        stem = re.sub(self.settings.ingest.file_names.sheet_suffix, "", Path(file_name).stem)
        found = self.eskd.find_designations(stem, file_name=True)
        if not found:
            return None
        rest = stem[found[0][2] :]
        rest = re.sub(r"^[\s_\-–.]+", "", rest).replace("_", " ").strip()
        return rest or None

    def _designation_systems(self, files: list[Path]) -> list[LogEntry]:
        """Which grammars read the kit, and a warning when most file names start with a designation none knows."""
        found: list[LogEntry] = []
        if self.eskd is not self.designations:
            names = ", ".join(self.eskd.grammars)
            message = f"Обозначения комплекта читаются по грамматикам {names}: их показывают имена файлов"
            found.append(LogEntry(level="info", message=message))
        share = self.settings.ingest.file_names.unknown_shape_share
        unknown = self.designations.unknown_shape([p.stem for p in files], share)
        if unknown is not None:
            look, count = unknown
            message = (
                f"Имена {count} файлов из {len(files)} начинаются с обозначения вида {look}, но ни одна грамматика "
                "его не знает: такие обозначения не читаются ни из имён файлов, ни из документов. Грамматики — раздел "
                "designations в configs/dictionaries.yaml"
            )
            found.append(LogEntry(level="warning", message=message))
        return found


def _stage(name: str, report_log: list[LogEntry], run: Callable[[], T], failed: T) -> T:
    """The result of a stage of the kit, or ``failed`` with an error in the log when it raises: one failed stage never
    costs the report."""
    try:
        return run()
    except Exception as exc:
        log.exception("Сбой стадии «%s»", name)
        message = f"Сбой стадии «{name}»: {type(exc).__name__}: {exc}. Её результатов в отчёте нет, остальное выполнено"
        report_log.append(LogEntry(level="error", message=message))
        return failed


def _unread(info: DocumentInfo) -> bool:
    """Whether a file may be a document of the kit the core did not read: a scan it could not read, a damaged or
    encrypted file, a file of a kind not told, or a drawing or specification without a designation."""
    if info.status in ("unreadable", "damaged", "encrypted"):
        return True
    readable = info.status in ("ok", "warning") and info.sheet_of is None
    return readable and (info.kind == "unknown" or (info.kind != "other" and not info.designation))


def _str(ex: Extracted | None) -> str | None:
    if ex is None or ex.value in (None, ""):
        return None
    return str(ex.value)


def _page_problems(doc: LoadedDocument) -> list[LogEntry]:
    """Pages not read or parsed go to the log; the file gets ``warning`` unless its status already says more.

    A blank page in a file with other pages is a note: a scanned back of a sheet or a spare form is no error.
    """
    blank = [p.number for p in doc.pages if p.blank]
    if blank and doc.info.status != "blank":
        numbers = ", ".join(map(str, blank))
        which = f"Пустой лист {numbers}" if len(blank) == 1 else f"Пустые листы {numbers}"
        doc.info.notes.append(f"{which} из {len(doc.pages)}: ни текста, ни линий, не распознаются")
    failed = [p for p in doc.pages if p.problem and not p.blank]
    if not failed:
        return []
    numbers = ", ".join(str(p.number) for p in failed)
    where = f"Ошибка на странице {numbers}" if len(failed) == 1 else f"Ошибки на страницах {numbers}"
    text = f"{where} из {len(doc.pages)}: остальные страницы обработаны"
    if doc.info.status == "ok":
        doc.info.status, doc.info.problem = "warning", text
    else:
        doc.info.notes.append(text)
    return [LogEntry(level="error", message=f"Страница {p.number}: {p.problem}", document_id=doc.id) for p in failed]


def _count_nodes(nodes: Iterable[TreeNode], pred: Callable[[TreeNode], bool] = lambda n: True) -> int:
    total = 0
    for n in nodes:
        if pred(n):
            total += 1
        total += _count_nodes(n.children, pred)
    return total
