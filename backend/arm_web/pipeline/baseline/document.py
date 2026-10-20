"""Приём файлов комплекта: определение формата, проверка целостности, извлечение текста.

Каждый файл превращается в :class:`LoadedDocument` со страницами и строками текста
(с координатами). Ошибки чтения не прерывают обработку — файл получает статус
``damaged``/``unsupported``/``unreadable`` и попадает в отчёт отдельной категорией.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter, ImageStat

from ...models import DocumentInfo, PageInfo, SourceRef
from . import ocr

log = logging.getLogger(__name__)
pymupdf.TOOLS.mupdf_display_errors(False)

MM_PT = 72 / 25.4
PAPER_MM = [(210, 297), (297, 420), (420, 594), (594, 841), (841, 1189), (297, 630), (297, 841)]
IMAGE_MEDIA = {"image/jpeg", "image/png", "image/tiff", "image/bmp"}


@dataclass
class Word:
    text: str
    bbox: tuple[float, float, float, float]

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Line:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    vertical: bool = False
    words: list[Word] = field(default_factory=list)
    confidence: float | None = None  # уверенность OCR (0..1); для текстового слоя — None

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Page:
    number: int
    width: float
    height: float
    unit: str = "pt"
    lines: list[Line] = field(default_factory=list)
    method: str | None = None  # text_layer | ocr | None (текст не извлечён)
    image_only: bool = False
    dpi: float | None = None

    @property
    def mm(self) -> float:
        """Единиц страницы на миллиметр."""
        if self.unit == "pt":
            return MM_PT
        if self.dpi:
            return self.dpi / 25.4
        # разрешение неизвестно: подбираем формат и типовое разрешение сканирования
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

    def norm_bbox(self, bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
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

    def source(self, page: Page, bbox, text: str | None = None, confidence: float | None = None) -> SourceRef:
        method = page.method if page.method in ("text_layer", "ocr") else None
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


def lines_confidence(lines) -> float | None:
    """Наименьшая уверенность OCR среди строк (None — текстовый слой)."""
    values = [ln.confidence for ln in lines if ln.confidence is not None]
    return round(min(values), 2) if values else None


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


def load_document(path: Path, root: Path, *, use_ocr: bool = True) -> LoadedDocument:
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
    try:
        if info.media_type == "application/pdf":
            _load_pdf(doc, data, use_ocr=use_ocr)
        else:
            _load_image(doc, data, use_ocr=use_ocr)
    except Exception as exc:  # повреждённый файл не должен останавливать обработку комплекта
        log.info("Не удалось прочитать %s: %s", rel, exc)
        info.status = "damaged"
        info.problem = f"Файл повреждён или не открывается: {_short(exc)}"
        doc.pages = []
        info.pages = []
    return doc


def _short(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) < 160 else text[:157] + "…"


def _load_pdf(doc: LoadedDocument, data: bytes, *, use_ocr: bool) -> None:
    pdf = pymupdf.open(stream=data, filetype="pdf")
    if pdf.needs_pass:
        raise ValueError("документ защищён паролем")
    if pdf.page_count == 0:
        raise ValueError("в документе нет страниц")
    text_pages = 0
    for index in range(pdf.page_count):
        pg = pdf[index]
        page = Page(number=index + 1, width=pg.rect.width, height=pg.rect.height, unit="pt")
        page.lines = _pdf_lines(pg)
        if page.lines:
            page.method = "text_layer"
            text_pages += 1
        else:
            page.image_only = bool(pg.get_images())
            pix = pg.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            if index == 0:
                doc.quality = image_quality(image)
            if use_ocr and ocr.available() and not is_unreadable(doc.quality):
                lines = ocr.recognize(image, scale=pg.rect.width / image.width)
                if lines:
                    page.lines, page.method = lines, "ocr"
        doc.pages.append(page)
    doc.info.origin = "vector" if text_pages == pdf.page_count else "scan" if text_pages == 0 else "mixed"
    _fill_page_info(doc)


def _pdf_lines(pg: pymupdf.Page) -> list[Line]:
    lines: list[Line] = []
    for block in pg.get_text("rawdict")["blocks"]:
        for ln in block.get("lines", []):
            words: list[Word] = []
            chars: list[tuple[str, tuple[float, float, float, float]]] = []
            for span in ln["spans"]:
                for ch in span["chars"]:
                    if ch["c"].isspace():
                        _flush_word(chars, words)
                    else:
                        chars.append((ch["c"], tuple(ch["bbox"])))
            _flush_word(chars, words)
            if not words:
                continue
            size = max(span["size"] for span in ln["spans"])
            vertical = abs(ln["dir"][0]) < 0.5
            text = " ".join(w.text for w in words)
            lines.append(Line(text=text, bbox=tuple(ln["bbox"]), size=size, vertical=vertical, words=words))
    return _split_wide_lines(lines)


def _flush_word(chars: list, words: list[Word]) -> None:
    if not chars:
        return
    text = "".join(c for c, _ in chars)
    boxes = [b for _, b in chars]
    words.append(
        Word(
            text,
            (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)),
        )
    )
    chars.clear()


def _split_wide_lines(lines: list[Line]) -> list[Line]:
    """Разбивает строку на части по большим горизонтальным разрывам (соседние графы таблиц)."""
    result: list[Line] = []
    for ln in lines:
        if ln.vertical or len(ln.words) < 2:
            result.append(ln)
            continue
        words = sorted(ln.words, key=lambda w: w.bbox[0])
        groups = [[words[0]]]
        for w in words[1:]:
            gap = w.bbox[0] - groups[-1][-1].bbox[2]
            if gap > ln.size * 1.6:
                groups.append([w])
            else:
                groups[-1].append(w)
        if len(groups) == 1:
            result.append(ln)
            continue
        for g in groups:
            bbox = (g[0].bbox[0], min(w.bbox[1] for w in g), g[-1].bbox[2], max(w.bbox[3] for w in g))
            result.append(
                Line(text=" ".join(w.text for w in g), bbox=bbox, size=ln.size, words=g, confidence=ln.confidence)
            )
    return result


def _load_image(doc: LoadedDocument, data: bytes, *, use_ocr: bool) -> None:
    with Image.open(io.BytesIO(data)) as probe:
        probe.verify()
    image = Image.open(io.BytesIO(data))
    image.load()
    frames = getattr(image, "n_frames", 1)
    dpi = image.info.get("dpi")
    dpi = float(dpi[0]) if dpi and dpi[0] and float(dpi[0]) > 50 else None
    doc.info.origin = "scan"
    for index in range(frames):
        image.seek(index)
        frame = image.convert("L")
        page = Page(number=index + 1, width=frame.width, height=frame.height, unit="px", image_only=True, dpi=dpi)
        if index == 0:
            doc.quality = image_quality(frame)
        if use_ocr and ocr.available() and not is_unreadable(doc.quality):
            lines = ocr.recognize(frame, scale=1.0)
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
    """Простые метрики читаемости скана: контраст и резкость (энергия границ)."""
    gray = image.convert("L")
    if gray.width > 1600:
        gray = gray.resize((1600, round(gray.height * 1600 / gray.width)))
    contrast = ImageStat.Stat(gray).stddev[0]
    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = ImageStat.Stat(edges).mean[0]
    dark = ImageStat.Stat(gray).mean[0]
    return {"contrast": round(contrast, 2), "sharpness": round(sharpness, 2), "brightness": round(dark, 2)}


def is_unreadable(quality: dict[str, float]) -> str | None:
    """Причина, по которой скан нечитаем, или None."""
    if not quality:
        return None
    if quality["contrast"] < 18:
        return "низкий контраст изображения"
    if quality["brightness"] < 60:
        return "слишком тёмное изображение"
    if quality["sharpness"] < 2.0:
        return "изображение размыто"
    return None
