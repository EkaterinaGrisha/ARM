"""Doc set file intake: format detection, integrity check, text extraction.

Each file becomes a :class:`LoadedDocument` with pages and text lines with coordinates. A read error never stops
the run: the file gets the ``damaged``, ``unsupported`` or ``unreadable`` status and its own report category.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image, ImageFilter, ImageStat

from kdcheck.ingest.pdf import PdfBackend, PdfPage, default_backend
from kdcheck.ingest.text import BBox, Line
from kdcheck.ocr import tesseract
from kdcheck.schemas import DocumentInfo, PageInfo, SourceRef

log = logging.getLogger(__name__)

MM_PT = 72 / 25.4
PAPER_MM = [(210, 297), (297, 420), (420, 594), (594, 841), (841, 1189), (297, 630), (297, 841)]
PDF_RENDER_DPI = 200  # pages without text that are not a single scan
SCAN_DPI_MAX = 400  # larger scans are reduced before OCR to bound time and memory
MAX_PAGES = 500  # pages read per file; bounds the time one huge file can take

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
    dpi: float | None = None

    @property
    def mm(self) -> float:
        """Page units per millimetre."""
        if self.unit == "pt":
            return MM_PT
        if self.dpi:
            return self.dpi / 25.4
        # Unknown resolution: pick the paper format and the typical scan resolution that fit best.
        short, long_ = sorted((self.width, self.height))
        best, best_err = short / 210, float("inf")
        for f_short, f_long in PAPER_MM:
            if abs(f_long / f_short - long_ / short) > 0.08:
                continue
            dpi = short / f_short * 25.4
            err = min(abs(dpi - typical) / typical for typical in (150, 200, 300, 400, 600))
            if err < best_err:
                best, best_err = short / f_short, err
        return best

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

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def has_text(self) -> bool:
        return any(p.lines for p in self.pages)

    def source(
        self, page: Page, bbox: BBox | None, text: str | None = None, confidence: float | None = None
    ) -> SourceRef:
        method = page.method
        if confidence is None:
            confidence = 0.95 if method == "text_layer" else 0.7 if method == "ocr" else None
        return SourceRef(
            document_id=self.id,
            page=page.number,
            bbox=page.norm_bbox(bbox) if bbox else None,
            text=text,
            method=method,
            confidence=confidence,
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
    *,
    use_ocr: bool = True,
    pdf_backend: PdfBackend | None = None,
    max_pages: int | None = MAX_PAGES,
) -> LoadedDocument:
    rel = path.relative_to(root).as_posix()
    data = path.read_bytes()
    info = DocumentInfo(
        id=document_id(rel),
        file_name=path.name,
        path=rel,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    doc = LoadedDocument(info=info, abs_path=path)
    if not data:
        info.status, info.problem = "damaged", "Пустой файл (0 байт)"
        return doc
    info.media_type = sniff_media_type(data[:2048])
    if info.media_type is None:
        info.status = "unsupported"
        info.problem = (
            f"Формат файла не поддерживается ({path.suffix or 'без расширения'}): ожидаются PDF или изображения"
        )
        return doc
    # A missing PDF backend is a broken installation, not a broken file: let it fail the run.
    backend = (pdf_backend or default_backend()) if info.media_type == "application/pdf" else None
    try:
        if backend is not None:
            _load_pdf(doc, data, backend, use_ocr=use_ocr, max_pages=max_pages)
        else:
            _load_image(doc, data, use_ocr=use_ocr, max_pages=max_pages)
    except Exception as exc:  # a damaged file must not stop the doc set
        log.info("Не удалось прочитать %s: %s", rel, exc)
        info.status = "damaged"
        info.problem = f"Файл повреждён или не открывается: {_short(exc)}"
        doc.pages = []
        info.pages = []
    return doc


def _short(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) < 160 else text[:157] + "…"


def _pages_to_read(doc: LoadedDocument, count: int, max_pages: int | None) -> int:
    if max_pages is None or count <= max_pages:
        return count
    doc.info.notes.append(f"Прочитано страниц: {max_pages} из {count}, остальные пропущены по лимиту на файл")
    return max_pages


def _load_pdf(doc: LoadedDocument, data: bytes, backend: PdfBackend, *, use_ocr: bool, max_pages: int | None) -> None:
    pdf = backend.open(data)
    if pdf.page_count == 0:
        raise ValueError("в документе нет страниц")
    count = _pages_to_read(doc, pdf.page_count, max_pages)
    text_pages = 0
    for index in range(count):
        pg = pdf.page(index)
        page = Page(number=index + 1, width=pg.width, height=pg.height, unit="pt")
        page.lines = pg.lines()
        if page.lines:
            page.method = "text_layer"
            text_pages += 1
        else:
            page.image_only = pg.has_images()
            image, origin, scale = _page_image(pg)
            if index == 0:
                doc.quality = image_quality(image)
            if use_ocr and tesseract.available() and not is_unreadable(doc.quality):
                lines = tesseract.recognize(image, scale=scale, origin=origin)
                if lines:
                    page.lines, page.method = lines, "ocr"
        doc.pages.append(page)
    doc.info.origin = "vector" if text_pages == count else "scan" if text_pages == 0 else "mixed"
    _fill_page_info(doc)


def _page_image(pg: PdfPage) -> tuple[Image.Image, tuple[float, float], float]:
    """Image to read a page without text: the scan itself when possible, else a render. Also where it sits."""
    found = pg.scan()
    if found is None:
        image = pg.render(PDF_RENDER_DPI / 72)
        return image, (0.0, 0.0), pg.width / image.width
    image, (x0, y0, x1, _) = found
    limit = round((x1 - x0) / 72 * SCAN_DPI_MAX)
    if image.width > limit:
        image = image.resize((limit, round(image.height * limit / image.width)), Image.Resampling.LANCZOS)
    return image, (x0, y0), (x1 - x0) / image.width


def _load_image(doc: LoadedDocument, data: bytes, *, use_ocr: bool, max_pages: int | None) -> None:
    with Image.open(io.BytesIO(data)) as probe:
        probe.verify()
    image = Image.open(io.BytesIO(data))
    image.load()
    frames = _pages_to_read(doc, getattr(image, "n_frames", 1), max_pages)
    raw_dpi = image.info.get("dpi")
    dpi = float(raw_dpi[0]) if raw_dpi and raw_dpi[0] and float(raw_dpi[0]) > 50 else None
    doc.info.origin = "scan"
    for index in range(frames):
        image.seek(index)
        frame = image.convert("L")
        page = Page(number=index + 1, width=frame.width, height=frame.height, unit="px", image_only=True, dpi=dpi)
        if index == 0:
            doc.quality = image_quality(frame)
        if use_ocr and tesseract.available() and not is_unreadable(doc.quality):
            lines = tesseract.recognize(frame, scale=1.0)
            if lines:
                page.lines, page.method = lines, "ocr"
        doc.pages.append(page)
    _fill_page_info(doc)


def _fill_page_info(doc: LoadedDocument) -> None:
    doc.info.pages = [
        PageInfo(
            number=p.number, width=round(p.width, 2), height=round(p.height, 2), unit=p.unit, has_text=bool(p.lines)
        )
        for p in doc.pages
    ]


def image_quality(image: Image.Image) -> dict[str, float]:
    """Simple scan readability metrics: contrast and sharpness (edge energy)."""
    gray = image.convert("L")
    if gray.width > 1600:
        gray = gray.resize((1600, round(gray.height * 1600 / gray.width)))
    contrast = ImageStat.Stat(gray).stddev[0]
    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = ImageStat.Stat(edges).mean[0]
    dark = ImageStat.Stat(gray).mean[0]
    return {"contrast": round(contrast, 2), "sharpness": round(sharpness, 2), "brightness": round(dark, 2)}


def is_unreadable(quality: dict[str, float]) -> str | None:
    """Why the scan is unreadable, or None."""
    if not quality:
        return None
    if quality["contrast"] < 18:
        return "низкий контраст изображения"
    if quality["brightness"] < 60:
        return "слишком тёмное изображение"
    if quality["sharpness"] < 2.0:
        return "изображение размыто"
    return None
