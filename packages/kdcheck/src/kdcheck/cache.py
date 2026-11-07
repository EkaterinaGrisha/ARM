"""Answers of OCR and of the model kept by the content of the request (ADR-0018): a second run reads nothing again.

Off unless a folder is given: ``kdcheck run --cache DIR``, ``ARM_CACHE_DIR`` or ``run(cache=…)``. A call that reads,
a Tesseract call or a request to the model, goes through :func:`lookup` with everything its answer depends on: the
engine and its version, the parameters of the request, the prompt and the hash of the pixels. The same parts give the
same key, so the answer read once is given back, and anything that changes the answer, a cropped cell with other
pixels or a new prompt, gives a new key and a new reading. A record is ``<folder>/<engine>/<ab>/<key>.json`` with the
answer and the parts of its key; images are not kept. Errors are not kept: the next run asks again. A record is
written to a temporary file and moved into place, so jobs that run at once never see half of one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image

log = logging.getLogger(__name__)

ENV = "ARM_CACHE_DIR"
T = TypeVar("T")

_folder: Path | None = None
stats: Counter[str] = Counter()  # «<engine>: hit» and «<engine>: miss» of this process


@contextmanager
def use(folder: Path | str | None) -> Iterator[None]:
    """Keep and give back answers in ``folder`` within the block; None or an empty string keep nothing. A process
    setting, not a context one: the readers of a sheet may run in threads of their own."""
    global _folder
    before, _folder = _folder, Path(folder) if folder else None
    try:
        yield
    finally:
        _folder = before


def folder_from_env() -> Path | None:
    value = os.environ.get(ENV, "").strip()
    return Path(value) if value else None


def pixels(image: Image.Image) -> str:
    """The SHA-256 of the mode, the size and the bytes of ``image``: the same pixels whatever file they came from."""
    digest = hashlib.sha256(f"{image.mode} {image.width}x{image.height} ".encode("ascii"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def lookup(engine: str, parts: Callable[[], dict[str, Any]], read: Callable[[], T]) -> T:
    """The answer kept for ``engine`` and the parts of its key, or the one ``read`` gives, kept for the next run.
    ``parts`` gives them only when a cache is on: hashing the pixels of a page costs a run without one nothing. The
    parts and the answer are JSON; an exception of ``read`` goes to the caller and nothing is kept."""
    if _folder is None:
        return read()
    given = parts()
    key = hashlib.sha256(json.dumps([engine, given], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    path = _folder / engine / key[:2] / f"{key}.json"
    try:
        answer: T = json.loads(path.read_text(encoding="utf-8"))["answer"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    else:
        stats[f"{engine}: hit"] += 1
        return answer
    answer = read()
    stats[f"{engine}: miss"] += 1
    _write(path, {"engine": engine, "parts": given, "answer": answer})
    return answer


def _write(path: Path, record: dict[str, Any]) -> None:
    """Atomically: a run that reads the record at once sees the whole of it or none. A cache that cannot be written,
    a full disk or a folder without access, costs the next run its time, never this one its answer."""
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out, ensure_ascii=False)
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        log.warning("Кэш прочтений не записан (%s): %s", path.parent, exc)
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


def summary() -> str:
    """«из кэша N, прочитано заново M» by engine, for the log of a run."""
    engines = sorted({key.split(":")[0] for key in stats})
    return "; ".join(f"{e}: из кэша {stats[f'{e}: hit']}, прочитано заново {stats[f'{e}: miss']}" for e in engines)
