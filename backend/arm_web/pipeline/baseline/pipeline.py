"""Базовый конвейер: правила поверх текстового слоя PDF и (если доступен) OCR.

Задуман как опорная реализация и пример интеграции: каждый этап можно заменить
моделью команды (VLM/LLM, свой OCR), сохранив формат отчёта.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from kdcheck.checks.completeness import DrawingRef, check
from kdcheck.extract import specification, title_block
from kdcheck.ingest.document import LoadedDocument, Page, is_unreadable, load_document
from kdcheck.normalize.designations import doc_code, find_designations, normalize_designation

from ...models import (
    Extracted,
    KitInfo,
    LogEntry,
    PartRecord,
    Report,
    SourceRef,
    Specification,
    Summary,
    TitleBlock,
)
from ..base import ProgressCallback, no_progress
from . import critical, requirements

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class BaselinePipeline:
    name = "baseline"
    version = "0.1.0"

    def __init__(self, use_ocr: bool = True):
        self.use_ocr = use_ocr

    # ------------------------------------------------------------------ run
    def run(self, input_dir: Path, *, kit_id: str, kit_name: str, progress: ProgressCallback = no_progress) -> Report:
        started = time.monotonic()
        report_log: list[LogEntry] = []
        files = sorted(p for p in input_dir.rglob("*") if p.is_file())
        progress("Чтение файлов", 0.0, f"Файлов в комплекте: {len(files)}")
        docs: list[LoadedDocument] = []
        for i, path in enumerate(files):
            progress("Чтение файлов", 0.6 * i / max(1, len(files)), path.relative_to(input_dir).as_posix())
            docs.append(load_document(path, input_dir, use_ocr=self.use_ocr))

        progress("Разбор документов", 0.6, None)
        specs: list[tuple[LoadedDocument, Specification]] = []
        parts: list[PartRecord] = []
        for i, doc in enumerate(docs):
            progress("Разбор документов", 0.6 + 0.25 * i / max(1, len(docs)), doc.info.path)
            if doc.info.status != "ok":
                continue
            try:
                self._classify_and_parse(doc, specs, parts)
            except Exception as exc:  # ошибка разбора одного файла не останавливает комплект
                log.exception("Ошибка разбора %s", doc.info.path)
                doc.info.status = "warning"
                doc.info.problem = f"Ошибка разбора: {exc}"
                report_log.append(LogEntry(level="error", message=f"Ошибка разбора: {exc}", document_id=doc.id))

        progress("Проверка комплектности", 0.87, None)
        drawing_refs = [
            DrawingRef(doc=d.info, part=next((p for p in parts if p.document_id == d.id), None))
            for d in docs
            if d.info.kind in ("part_drawing", "assembly_drawing", "drawing") and d.info.designation
        ]
        result = check([(d.info, s) for d, s in specs], drawing_refs)
        report_log += [LogEntry(level="warning", message=m) for m in result.log]

        progress("Анализ требований", 0.93, None)
        criticals = critical.select(parts)

        progress("Формирование отчёта", 0.97, None)
        infos = [d.info for d in docs]
        for d in infos:
            if d.status in ("damaged", "unsupported", "unreadable", "foreign"):
                report_log.append(LogEntry(level="warning", message=d.problem or d.status, document_id=d.id))
        tree_items = _count_nodes(result.tree)
        need = _count_nodes(
            result.tree, lambda n: n.status in ("ok", "missing", "warning") and n.kind in ("part", "document")
        )
        have = _count_nodes(result.tree, lambda n: n.kind in ("part", "document") and bool(n.drawing_document_ids))
        summary = Summary(
            documents_total=len(infos),
            documents_by_status=dict(Counter(d.status for d in infos)),
            documents_by_kind=dict(Counter(d.kind for d in infos)),
            specifications=len(specs),
            drawings=len(drawing_refs),
            tree_items=tree_items,
            parts=len(parts),
            parameters=sum(len(p.parameters) for p in parts),
            discrepancies_total=len(result.discrepancies),
            discrepancies_by_type=dict(Counter(x.type for x in result.discrepancies)),
            critical_parameters=len(criticals),
            completeness_ratio=round(have / need, 4) if need else None,
        )
        kit = KitInfo(
            id=kit_id,
            name=kit_name,
            root_designation=result.root_designation,
            root_name=result.root_name,
            processed_at=_now(),
            duration_sec=round(time.monotonic() - started, 2),
            pipeline_name=self.name,
            pipeline_version=self.version,
        )
        progress("Готово", 1.0, None)
        return Report(
            kit=kit,
            summary=summary,
            documents=infos,
            specifications=[s for _, s in specs],
            tree=result.tree,
            discrepancies=result.discrepancies,
            parts=parts,
            critical_parameters=criticals,
            log=report_log,
        )

    # ------------------------------------------------------------------ classification
    def _classify_and_parse(self, doc: LoadedDocument, specs, parts) -> None:
        info = doc.info
        text_pages = [p for p in doc.pages if p.lines]
        if not text_pages:
            self._scan_without_text(doc, parts)
            return
        if any(specification.is_specification(p) for p in text_pages[:2]):
            spec = specification.parse(doc)
            info.kind = "specification"
            info.designation = _str(spec.designation) or _filename_designation(info.file_name)
            info.name = _str(spec.name)
            if not spec.rows:
                info.status, info.problem = "warning", "Спецификация распознана, но строки таблицы не найдены"
            specs.append((doc, spec))
            self._check_filename(doc)
            return
        page = text_pages[0]
        geom = title_block.locate(page)
        if geom is not None and geom.form == 2:
            spec = specification.parse(doc)
            info.kind = "specification"
            info.designation = _str(spec.designation)
            info.name = _str(spec.name)
            specs.append((doc, spec))
            return
        if geom is None:
            fallback = title_block.default_geometry(page, 1)
            has_designation = any(
                find_designations(ln.text) for ln in title_block.lines_in(page, fallback.rect((40, -6, 185, 18)))
            )
            if page.method == "ocr" and has_designation:
                geom = fallback
            else:
                if page.method == "ocr" and is_unreadable(doc.quality):
                    info.status, info.problem = "unreadable", f"Нечитаемый скан: {is_unreadable(doc.quality)}"
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
        info = doc.info
        tb = title_block.parse(doc, page, geom)
        if tb.designation is None:
            fname = _filename_designation(info.file_name)
            if fname:
                tb.designation = Extracted(
                    value=fname,
                    raw=info.file_name,
                    confidence=0.5,
                    source=SourceRef(
                        document_id=doc.id, page=1, method="filename", text=info.file_name, confidence=0.5
                    ),
                )
                info.notes.append("Обозначение в основной надписи не распознано — взято из имени файла")
        designation = _str(tb.designation)
        name = _str(tb.name)
        assembly = bool(designation and doc_code(designation) == "СБ") or "сборочный чертеж" in (
            name or ""
        ).lower().replace("ё", "е")
        info.kind = "assembly_drawing" if assembly else "part_drawing"
        info.designation = designation
        info.name = name
        if page.method == "ocr":
            info.notes.append("Текст получен распознаванием скана (OCR) — значения требуют проверки")
        part = PartRecord(document_id=doc.id, designation=designation, name=name, kind=info.kind, title_block=tb)

        counter = iter(range(1, 10_000))

        def next_id() -> str:
            return f"{doc.id}-p{next(counter)}"

        tt_lines = requirement_lines(page, geom)
        part.requirements = requirements.requirement_items(doc, page, tt_lines)
        for item in part.requirements:
            part.parameters += requirements.parse_requirement(item, next_id)
        skip = {id(ln) for ln in tt_lines}
        tb_x0, tb_y0 = geom.x0 - 1 * geom.s, geom.y0 - 1 * geom.s
        for pg in doc.pages:
            for ln in pg.lines:
                if ln.vertical or id(ln) in skip:
                    continue
                if pg is page and ln.cx >= tb_x0 and ln.cy >= tb_y0:
                    continue  # основная надпись
                if ln.bbox[0] < 21 * pg.mm:
                    continue  # поле подшивки
                general_zone = ln.cy < 30 * pg.mm and ln.cx > pg.width - 90 * pg.mm
                part.parameters += requirements.parse_annotation(doc, pg, ln, next_id, general_zone=general_zone)
        return part

    def _scan_without_text(self, doc: LoadedDocument, parts: list[PartRecord]) -> None:
        info = doc.info
        reason = is_unreadable(doc.quality)
        if reason:
            info.status, info.problem = "unreadable", f"Нечитаемый скан: {reason}"
            return
        des = _filename_designation(info.file_name)
        info.status = "warning"
        if not des:
            info.problem = "Скан без текстового слоя: распознавание (OCR) недоступно, обозначение не определено"
            return
        info.problem = "Скан без текстового слоя: распознавание (OCR) недоступно, обозначение взято из имени файла"
        info.designation = des
        info.name = _filename_name(info.file_name)
        info.kind = "assembly_drawing" if doc_code(des) == "СБ" else "part_drawing"
        src = SourceRef(document_id=doc.id, page=1, method="filename", text=info.file_name, confidence=0.5)
        tb = TitleBlock(designation=Extracted(value=des, raw=info.file_name, confidence=0.5, source=src))
        if info.name:
            tb.name = Extracted(value=info.name, raw=info.file_name, confidence=0.4, source=src)
        parts.append(PartRecord(document_id=doc.id, designation=des, name=info.name, kind=info.kind, title_block=tb))

    def _check_filename(self, doc: LoadedDocument) -> None:
        fname = _filename_designation(doc.info.file_name)
        if (
            fname
            and doc.info.designation
            and fname != doc.info.designation
            and fname != doc.info.designation.split(" ")[0]
        ):
            doc.info.notes.append(
                f"Обозначение в имени файла ({fname}) отличается от основной надписи ({doc.info.designation})"
            )


def requirement_lines(page: Page, geom: title_block.Geometry):
    return requirements.requirement_lines(page, geom)


def _str(ex: Extracted | None) -> str | None:
    if ex is None or ex.value in (None, ""):
        return None
    return str(ex.value)


def _filename_designation(file_name: str) -> str | None:
    stem = Path(file_name).stem
    return normalize_designation(stem)


def _filename_name(file_name: str) -> str | None:
    stem = Path(file_name).stem
    found = find_designations(stem)
    if not found:
        return None
    rest = stem[found[0][2] :]
    rest = re.sub(r"^[\s_\-–.]+", "", rest).replace("_", " ").strip()
    return rest or None


def _count_nodes(nodes, pred=lambda n: True) -> int:
    total = 0
    for n in nodes:
        if pred(n):
            total += 1
        total += _count_nodes(n.children, pred)
    return total
