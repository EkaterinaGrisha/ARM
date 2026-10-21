"""Page rasters for the document viewer in the web UI."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from kdcheck.ingest.document import sniff_media_type
from kdcheck.ingest.pdf import get_backend


class RenderError(Exception):
    pass


def render_page(path: Path, page: int, *, max_px: int, pdf_backend: str) -> bytes:
    """PNG of page ``page`` (from 1) with the long side at most ``max_px``; ``pdf_backend`` as in ``models.yaml``."""
    data = path.read_bytes()
    media = sniff_media_type(data[:2048])
    backend = get_backend(pdf_backend) if media == "application/pdf" else None
    try:
        if backend is not None:
            doc = backend.open(data)
            if not 1 <= page <= doc.page_count:
                raise RenderError(f"В документе нет страницы {page}")
            pg = doc.page(page - 1)
            img = pg.render(max_px / max(pg.width, pg.height))
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
    # Drawings are nearly monochrome: 16 gray levels shrink the file a lot and stay readable.
    img.convert("L").quantize(colors=16).save(out, "PNG", optimize=True)
    return out.getvalue()
