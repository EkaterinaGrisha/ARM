"""Растеризация страниц документов для просмотра в веб-интерфейсе."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

from .pipeline.baseline.document import sniff_media_type

pymupdf.TOOLS.mupdf_display_errors(False)


class RenderError(Exception):
    pass


def render_page(path: Path, page: int, max_px: int = 2000) -> bytes:
    """PNG страницы ``page`` (с 1); длинная сторона — не больше ``max_px``."""
    data = path.read_bytes()
    media = sniff_media_type(data[:2048])
    try:
        if media == "application/pdf":
            doc = pymupdf.open(stream=data, filetype="pdf")
            if not 1 <= page <= doc.page_count:
                raise RenderError(f"В документе нет страницы {page}")
            pg = doc[page - 1]
            zoom = max_px / max(pg.rect.width, pg.rect.height)
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
        elif media and media.startswith("image/"):
            img = Image.open(io.BytesIO(data))
            if page > getattr(img, "n_frames", 1):
                raise RenderError(f"В документе нет страницы {page}")
            img.seek(page - 1)
            img = img.convert("L")
            if max(img.size) > max_px:
                img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        else:
            raise RenderError("Формат не поддерживает просмотр")
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"Не удалось отрисовать страницу: {exc}") from exc
    out = io.BytesIO()
    # чертежи почти монохромны: 16 оттенков серого сильно уменьшают размер без потери читаемости
    img.convert("L").quantize(colors=16).save(out, "PNG", optimize=True)
    return out.getvalue()
