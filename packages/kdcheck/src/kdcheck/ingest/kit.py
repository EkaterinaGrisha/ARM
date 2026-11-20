"""Reading the files of a kit, in several processes when OCR reads them (plan, item «г» of 19.11).

A file is read as :func:`~kdcheck.ingest.document.load_document` reads it alone, so the documents and the report do not
depend on the number of processes or on the order the files finish in. Byte copies are found before reading: each
file learns the first file of the kit with its bytes. A process that dies, killed for memory or crashed in a library,
costs only the speed: the files it left unread are read again one by one in the job process, as without a pool.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from kdcheck import cache
from kdcheck.config import Settings
from kdcheck.ingest.document import LoadedDocument, load_document
from kdcheck.ingest.pdf import PdfBackend
from kdcheck.ocr import tesseract

log = logging.getLogger(__name__)

_settings: Settings | None = None  # of a reading process, set once when it starts


def first_copies(paths: list[Path], root: Path) -> dict[str, str]:
    """SHA-256 of the bytes → the path of the first file of ``paths`` with them; a file that cannot be read has none."""
    first: dict[str, str] = {}
    for path in paths:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        first.setdefault(digest, path.relative_to(root).as_posix())
    return first


def load_kit(
    paths: list[Path], root: Path, settings: Settings, pdf_backend: PdfBackend, done: Callable[[int, Path], None]
) -> tuple[list[LoadedDocument], str | None]:
    """The documents of ``paths`` in their order and, when a reading process died, a line for the log of the report.

    Files are read in ``ingest.processes`` processes when OCR is on: text layers alone are read faster than the
    processes start. ``done`` gets the count of files read and the last of them."""
    copies = first_copies(paths, root)
    ocr = settings.models.ocr
    workers = min(settings.ingest.processes, len(paths))
    pooled = workers > 1 and ocr.enabled and tesseract.available(ocr.languages)
    read: dict[int, LoadedDocument] = {}
    if pooled:
        # spawn, not fork: a forked child would inherit the threads of onnxruntime and of the caller mid-way
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(workers, mp_context=context, initializer=_start, initargs=(settings,)) as pool:
            futures: dict[Future[tuple[LoadedDocument, Counter[str]]], int] = {
                pool.submit(_load, path, root, copies, cache.folder()): i for i, path in enumerate(paths)
            }
            try:
                for future in as_completed(futures):
                    try:
                        doc, counts = future.result()
                    except BrokenProcessPool:
                        continue
                    cache.stats.update(counts)
                    read[futures[future]] = doc
                    done(len(read), paths[futures[future]])
            except BaseException:  # a broken installation fails the run at once, not after the files queued
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    left = [i for i in range(len(paths)) if i not in read]
    for i in left:
        read[i] = load_document(paths[i], root, settings, pdf_backend=pdf_backend, known=copies)
        done(len(read), paths[i])
    note = None
    if pooled and left:
        note = f"Процесс чтения файлов завершился аварийно; оставшиеся файлы ({len(left)}) прочитаны по одному"
        log.warning(note)
    return [read[i] for i in range(len(paths))], note


def _start(settings: Settings) -> None:
    global _settings
    _settings = settings


def _load(path: Path, root: Path, copies: dict[str, str], folder: Path | None) -> tuple[LoadedDocument, Counter[str]]:
    """One file read in a reading process, and the hits and misses of the cache while it was read."""
    assert _settings is not None
    before = Counter(cache.stats)
    with cache.use(folder):
        doc = load_document(path, root, _settings, known=copies)
    return doc, cache.stats - before
