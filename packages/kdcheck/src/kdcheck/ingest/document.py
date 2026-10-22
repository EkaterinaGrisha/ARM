"""Doc set file intake: format detection, integrity check, text extraction.

Each file becomes a :class:`LoadedDocument` with pages and text lines with coordinates. A read error never stops
the run: the file gets the ``damaged``, ``encrypted``, ``unsupported`` or ``unreadable`` status and its own report
category. A byte copy of a file read before gets ``duplicate_file`` and is not read again.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image, ImageFilter, ImageStat

from kdcheck.config import ScanQuality, Settings
from kdcheck.ingest.pdf import EncryptedPdfError, PdfBackend, PdfPage, get_backend
from kdcheck.ingest.text import BBox, Line
from kdcheck.ocr import tesseract
from kdcheck.schemas import DocumentInfo, PageInfo, SourceRef

log = logging.getLogger(__name__)

PT_PER_INCH = 72
MM_PER_INCH = 25.4
MM_PT = PT_PER_INCH / MM_PER_INCH

TextMethod = Literal["text_layer", "ocr"]


@dataclass
class Page:
    number: int
    width: float
    height: float
    unit: Literal["pt", "px"] = "pt"
    lines: list[Line] = field(default_factory=list)
    method: TextMethod | None = None  # None: no text extracted
    image_only: bool = False
    dpi: float | None = None  # of an image page: from its metadata or estimated
    engine: str | None = None  # what read the text, and its version
    engine_version: str | None = None
    confidence: float | None = None  # of values read by ``method`` when the lines give none

    @property
    def mm(self) -> float:
        """Page units per millimetre."""
        if self.unit == "pt":
            return MM_PT
        if not self.dpi:
            raise ValueError("an image page needs its resolution")
        return self.dpi / MM_PER_INCH

    def read_by(self, method: TextMethod, engine: str, version: str, confidence: float) -> None:
        self.method, self.engine, self.engine_version, self.confidence = method, engine, version, confidence

    def norm_bbox(self, bbox: BBox) -> BBox:
        x0, y0, x1, y1 = bbox
        return (
            round(max(0.0, x0 / self.width), 4),
            round(max(0.0, y0 / self.height), 4),
            round(min(1.0, x1 / self.width), 4),
            round(min(1.0, y1 / self.height), 4),
        )


@dataclass
class LoadedDocument:
    info: DocumentInfo
    abs_path: Path
    pages: list[Page] = field(default_factory=list)
    quality: dict[str, float] = field(default_factory=dict)
    unreadable: str | None = None  # why the scan is unreadable

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def has_text(self) -> bool:
        return any(p.lines for p in self.pages)

    def source(
        self, page: Page, bbox: BBox | None, text: str | None = None, confidence: float | None = None
    ) -> SourceRef:
        return SourceRef(
            document_id=self.id,
            page=page.number,
            bbox=page.norm_bbox(bbox) if bbox else None,
            text=text,
            method=page.method,
            confidence=page.confidence if confidence is None else confidence,
            engine=page.engine,
            engine_version=page.engine_version,
        )


def document_id(rel_path: str) -> str:
    return "d" + hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:10]


def sniff_media_type(head: bytes) -> str | None:
    if head.startswith(b"%PDF") or b"%PDF-" in head[:1024]:
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head.startswith(b"BM"):
        return "image/bmp"
    return None


def load_document(
    path: Path,
    root: Path,
    settings: Settings,
    *,
    pdf_backend: PdfBackend | None = None,
    known: Mapping[str, str] | None = None,
) -> LoadedDocument:
    """Read one file of the kit. ``known`` maps the SHA-256 of the files read before to their paths."""
    rel = path.relative_to(root).as_posix()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    info = DocumentInfo(id=document_id(rel), file_name=path.name, path=rel, size_bytes=len(data), sha256=digest)
    doc = LoadedDocument(info=info, abs_path=path)
    if not data:
        info.status, info.problem = "damaged", "Пустой файл (0 байт)"
        return doc
    info.media_type = sniff_media_type(data[:2048])
    if known and digest in known:
        info.status = "duplicate_file"
        info.problem = f"Побайтовая копия файла {known[digest]}: повторно не разбирается"
        return doc
    if info.media_type is None:
        info.status = "unsupported"
        info.problem = (
            f"Формат файла не поддерживается ({path.suffix or 'без расширения'}): ожидаются PDF или изображения"
        )
        return doc
    # A missing PDF backend is a broken installation, not a broken file: let it fail the run.
    pdf = info.media_type == "application/pdf"
    backend = (pdf_backend or get_backend(settings.models.pdf.backend)) if pdf else None
    try:
        if backend is not None:
            _load_pdf(doc, data, backend, settings)
        else:
            _load_image(doc, data, settings)
    except Exception as exc:  # a damaged file must not stop the doc set
        if isinstance(exc, EncryptedPdfError):
            info.status, info.problem = "encrypted", "PDF защищён паролем: без пароля содержимое не прочитать"
        else:
            log.info("Не удалось прочитать %s: %s", rel, exc)
            info.status = "damaged"
            info.problem = f"Файл повреждён или не открывается: {_short(exc)}"
        doc.pages = []
        info.pages = []
    return doc


def _short(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) < 160 else text[:157] + "…"


def _use_ocr(settings: Settings) -> bool:
    ocr = settings.models.ocr
    return ocr.enabled and tesseract.available(ocr.languages)


def _ocr(page: Page, image: Image.Image, settings: Settings, scale: float, origin: tuple[float, float]) -> None:
    ocr = settings.models.ocr
    lines = tesseract.recognize(image, ocr, settings.ingest.text, scale=scale, origin=origin)
    if lines:
        page.lines = lines
        page.read_by("ocr", ocr.engine, tesseract.version(), settings.ingest.confidence.ocr)


def _pages_to_read(doc: LoadedDocument, count: int, max_pages: int) -> int:
    if not max_pages or count <= max_pages:  # 0: no limit
        return count
    doc.info.notes.append(f"Прочитано страниц: {max_pages} из {count}, остальные пропущены по лимиту на файл")
    return max_pages


def _load_pdf(doc: LoadedDocument, data: bytes, backend: PdfBackend, settings: Settings) -> None:
    ingest = settings.ingest
    use_ocr = _use_ocr(settings)
    pdf = backend.open(data)
    if pdf.page_count == 0:
        raise ValueError("в документе нет страниц")
    count = _pages_to_read(doc, pdf.page_count, ingest.max_pages_per_file)
    version = backend.versions()[backend.name]
    text_pages = 0
    for index in range(count):
        pg = pdf.page(index)
        page = Page(number=index + 1, width=pg.width, height=pg.height, unit="pt")
        page.lines = pg.lines(ingest.text)
        if page.lines:
            page.read_by("text_layer", backend.name, version, ingest.confidence.text_layer)
            text_pages += 1
        else:
            page.image_only = pg.has_images()
            image, origin, scale = _page_image(pg, settings)
            if index == 0:
                doc.quality = image_quality(image, ingest.quality.sample_width)
                doc.unreadable = unreadable_reason(doc.quality, ingest.quality)
            if use_ocr and not doc.unreadable:
                _ocr(page, image, settings, scale, origin)
        doc.pages.append(page)
    doc.info.origin = "vector" if text_pages == count else "scan" if text_pages == 0 else "mixed"
    _fill_page_info(doc)


def _page_image(pg: PdfPage, settings: Settings) -> tuple[Image.Image, tuple[float, float], float]:
    """Image to read a page without text: the scan itself when possible, else a render. Also where it sits."""
    ingest = settings.ingest
    found = pg.scan(ingest.scan)
    if found is None:
        image = pg.render(ingest.render_dpi / PT_PER_INCH)
        return image, (0.0, 0.0), pg.width / image.width
    image, (x0, y0, x1, _) = found
    limit = round((x1 - x0) / PT_PER_INCH * ingest.scan.max_dpi)
    if image.width > limit:
        image = image.resize((limit, round(image.height * limit / image.width)), Image.Resampling.LANCZOS)
    return image, (x0, y0), (x1 - x0) / image.width


def _load_image(doc: LoadedDocument, data: bytes, settings: Settings) -> None:
    ingest = settings.ingest
    use_ocr = _use_ocr(settings)
    with Image.open(io.BytesIO(data)) as probe:
        probe.verify()
    image = Image.open(io.BytesIO(data))
    image.load()
    frames = _pages_to_read(doc, getattr(image, "n_frames", 1), ingest.max_pages_per_file)
    raw_dpi = image.info.get("dpi")
    dpi = float(raw_dpi[0]) if raw_dpi and raw_dpi[0] and float(raw_dpi[0]) > ingest.min_image_dpi else None
    doc.info.origin = "scan"
    for index in range(frames):
        image.seek(index)
        frame = image.convert("L")
        page = Page(
            number=index + 1,
            width=frame.width,
            height=frame.height,
            unit="px",
            image_only=True,
            dpi=dpi or estimated_dpi(frame.width, frame.height, settings),
        )
        if index == 0:
            doc.quality = image_quality(frame, ingest.quality.sample_width)
            doc.unreadable = unreadable_reason(doc.quality, ingest.quality)
        if use_ocr and not doc.unreadable:
            _ocr(page, frame, settings, 1.0, (0.0, 0.0))
        doc.pages.append(page)
    _fill_page_info(doc)


def estimated_dpi(width: float, height: float, settings: Settings) -> float:
    """Resolution of an image without metadata: the paper format and typical scan resolution that fit it best."""
    rules, formats = settings.ingest.page_scale, settings.forms.sheet.formats
    short, long_ = sorted((width, height))
    best, best_err = short / formats[rules.fallback_format][0], float("inf")
    for f_short, f_long in formats.values():
        if abs(f_long / f_short - long_ / short) > rules.aspect_tolerance:
            continue
        dpi = short / f_short * MM_PER_INCH
        err = min(abs(dpi - typical) / typical for typical in rules.typical_dpi)
        if err < best_err:
            best, best_err = short / f_short, err
    return best * MM_PER_INCH


def _fill_page_info(doc: LoadedDocument) -> None:
    doc.info.pages = [
        PageInfo(
            number=p.number, width=round(p.width, 2), height=round(p.height, 2), unit=p.unit, has_text=bool(p.lines)
        )
        for p in doc.pages
    ]


def image_quality(image: Image.Image, sample_width: int) -> dict[str, float]:
    """Simple scan readability metrics: contrast and sharpness (edge energy)."""
    gray = image.convert("L")
    if gray.width > sample_width:
        gray = gray.resize((sample_width, round(gray.height * sample_width / gray.width)))
    contrast = ImageStat.Stat(gray).stddev[0]
    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = ImageStat.Stat(edges).mean[0]
    dark = ImageStat.Stat(gray).mean[0]
    return {"contrast": round(contrast, 2), "sharpness": round(sharpness, 2), "brightness": round(dark, 2)}


def unreadable_reason(quality: dict[str, float], rules: ScanQuality) -> str | None:
    """Why the scan is unreadable, or None."""
    if not quality:
        return None
    if quality["contrast"] < rules.min_contrast:
        return "низкий контраст изображения"
    if quality["brightness"] < rules.min_brightness:
        return "слишком тёмное изображение"
    if quality["sharpness"] < rules.min_sharpness:
        return "изображение размыто"
    return None
