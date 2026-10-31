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

from kdcheck.checks import rules
from kdcheck.checks.completeness import check
from kdcheck.config import Settings, settings_sha256
from kdcheck.extract import review, specification, tech_requirements, title_block
from kdcheck.ingest.document import LoadedDocument, Page, load_document, page_raster
from kdcheck.ingest.pdf import get_backend
from kdcheck.normalize.designations import Designations
from kdcheck.ocr import tesseract
from kdcheck.params import critical, parameters
from kdcheck.schemas import (
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
from kdcheck.tree.product import DrawingRef, build_tree
from kdcheck.vlm import title_block as vlm_title_block
from kdcheck.vlm.client import VlmClient, VlmError, load_prompt

log = logging.getLogger(__name__)

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
        self.eskd = Designations(settings.dictionaries)
        self.rules = rules.configured(settings.checks)
        self.parameters = parameters.Parser(settings.dictionaries)
        vlm = settings.models.vlm
        self.vlm = VlmClient(vlm, load_prompt(vlm.prompt)) if vlm.enabled else None
        self.vlm_problem: str | None = None  # why the model is not asked in this run
        self.vlm_used = False

    # ------------------------------------------------------------------ run
    def run(self, input_dir: Path, *, kit_id: str, kit_name: str, progress: ProgressCallback = no_progress) -> Report:
        started = time.monotonic()
        report_log: list[LogEntry] = []
        self.vlm_problem = self.vlm.unavailable() if self.vlm is not None else None
        self.vlm_used = False
        files = sorted(p for p in input_dir.rglob("*") if p.is_file())
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

        progress("Проверка комплектности", 0.87, None)
        drawing_refs = [
            DrawingRef(doc=d.info, part=next((p for p in parts if p.document_id == d.id), None))
            for d in docs
            if d.info.kind in ("part_drawing", "assembly_drawing", "drawing") and d.info.designation
        ]
        tree = build_tree([(d.info, s) for d, s in specs], drawing_refs, self.eskd)
        discrepancies = check(tree, self.eskd, self.rules, self.settings.checks.names)
        report_log += [LogEntry(level="warning", message=m) for m in tree.log]

        progress("Анализ требований", 0.93, None)
        criticals = critical.select(parts, self.settings.criticality)

        progress("Формирование отчёта", 0.97, None)
        for model in [*parts, *(s for _, s in specs)]:
            review.mark(model, self.settings.ingest.review)
        infos = [d.info for d in docs]
        for d in infos:
            if d.status in ("damaged", "encrypted", "unsupported", "unreadable", "foreign", "duplicate_file"):
                report_log.append(LogEntry(level="warning", message=d.problem or d.status, document_id=d.id))
        tree_items = _count_nodes(tree.roots)
        need = _count_nodes(
            tree.roots, lambda n: n.status in ("ok", "missing", "warning") and n.kind in ("part", "document")
        )
        have = _count_nodes(tree.roots, lambda n: n.kind in ("part", "document") and bool(n.drawing_document_ids))
        summary = Summary(
            documents_total=len(infos),
            documents_by_status=dict(Counter(d.status for d in infos)),
            documents_by_kind=dict(Counter(d.kind for d in infos)),
            specifications=len(specs),
            drawings=len(drawing_refs),
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
            root_designation=tree.root_designation,
            root_name=tree.root_name,
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
            tree=tree.roots,
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
        if self.vlm is not None and self.vlm_used:
            found.append(EngineInfo(name=self.vlm.engine.model, version=self.vlm.version))
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
            info.kind = "specification"
            info.designation = _str(spec.designation) or self._filename_designation(info.file_name)
            info.name = _str(spec.name)
            if not spec.rows:
                info.status, info.problem = "warning", "Спецификация распознана, но строки таблицы не найдены"
            specs.append((doc, spec))
            self._check_filename(doc)
            return
        page = text_pages[0]
        geom = title_block.locate(page, forms)
        if geom is not None and geom.form == 2:
            spec = specification.parse(doc, forms, self.eskd)
            info.kind = "specification"
            info.designation = _str(spec.designation)
            info.name = _str(spec.name)
            specs.append((doc, spec))
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
        self._check_filename(doc)

    def _parse_drawing(self, doc: LoadedDocument, page: Page, geom: title_block.Geometry) -> PartRecord:
        info, forms = doc.info, self.settings.forms
        tb = title_block.parse(doc, page, geom, forms, self.eskd)
        if self.vlm is not None and self.vlm_problem is None and page.method in self.settings.models.vlm.methods:
            self._read_cells(doc, page, geom, tb)
        if tb.designation is None:
            fname = self._filename_designation(info.file_name)
            if fname:
                confidence = self.settings.ingest.confidence.filename
                tb.designation = Extracted(
                    value=fname,
                    raw=info.file_name,
                    confidence=confidence,
                    source=SourceRef(
                        document_id=doc.id, page=1, method="filename", text=info.file_name, confidence=confidence
                    ),
                )
                info.notes.append("Обозначение в основной надписи не распознано — взято из имени файла")
        designation = _str(tb.designation)
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
        for item in part.requirements:
            part.parameters += self.parameters.requirement(item, next_id)
        skip = {id(ln) for ln in tt_lines}
        edge = forms.title_block.edge_tolerance
        tb_x0, tb_y0 = geom.x0 - edge * geom.s, geom.y0 - edge * geom.s
        sheet = forms.sheet
        top, right = sheet.general_roughness_zone
        for pg in doc.pages:
            found: list[Parameter] = []
            try:
                for ln in pg.lines:
                    if ln.vertical or id(ln) in skip:
                        continue
                    if pg is page and ln.cx >= tb_x0 and ln.cy >= tb_y0:
                        continue  # title block
                    if ln.bbox[0] < sheet.binding_margin * pg.mm:
                        continue  # binding margin
                    general_zone = ln.cy < top * pg.mm and ln.cx > pg.width - right * pg.mm
                    found += self.parameters.annotation(doc, pg, ln, next_id, general_zone=general_zone)
            except Exception as exc:  # a page that fails to parse never costs the parameters of the others
                log.exception("Ошибка разбора страницы %s в %s", pg.number, info.path)
                doc.page_failed(pg, f"ошибка разбора параметров: {exc}")
                continue
            part.parameters += found
        return part

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

    def _check_filename(self, doc: LoadedDocument) -> None:
        fname = self._filename_designation(doc.info.file_name)
        if (
            fname
            and doc.info.designation
            and fname != doc.info.designation
            and fname != doc.info.designation.split(" ")[0]
        ):
            doc.info.notes.append(
                f"Обозначение в имени файла ({fname}) отличается от основной надписи ({doc.info.designation})"
            )

    def _filename_designation(self, file_name: str) -> str | None:
        return self.eskd.normalize_designation(Path(file_name).stem)

    def _filename_name(self, file_name: str) -> str | None:
        stem = Path(file_name).stem
        found = self.eskd.find_designations(stem)
        if not found:
            return None
        rest = stem[found[0][2] :]
        rest = re.sub(r"^[\s_\-–.]+", "", rest).replace("_", " ").strip()
        return rest or None


def _str(ex: Extracted | None) -> str | None:
    if ex is None or ex.value in (None, ""):
        return None
    return str(ex.value)


def _page_problems(doc: LoadedDocument) -> list[LogEntry]:
    """Pages not read or parsed go to the log; the file gets ``warning`` unless its status already says more."""
    failed = [p for p in doc.pages if p.problem]
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
