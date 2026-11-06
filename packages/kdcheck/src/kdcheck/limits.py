"""A time limit on reading one sheet (plan stage 3): a sheet that reads too long ends alone, the others are read.

The page loop of :mod:`kdcheck.ingest.document` sets the limit around each sheet, ``page_timeout_sec`` in
``configs/ingest.yaml``. Long steps call :func:`check` between their pieces, and each Tesseract call gets
:func:`remaining` as its timeout, so a hung recognizer is stopped too. A step inside one library call, such as PDFium
rendering a page, cannot be stopped from here: the time limit of the whole job, ``job_timeout_sec`` in
``configs/api.yaml``, covers it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("sheet_deadline", default=None)


class SheetTimeout(Exception):
    """The sheet has been read longer than its limit."""


@contextmanager
def sheet_limit(seconds: float) -> Iterator[None]:
    """Read one sheet within ``seconds``; 0 sets no limit."""
    token = _deadline.set(time.monotonic() + seconds if seconds > 0 else None)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining() -> float | None:
    """The seconds left for the sheet, None without a limit; past the limit, :class:`SheetTimeout`."""
    deadline = _deadline.get()
    if deadline is None:
        return None
    left = deadline - time.monotonic()
    if left <= 0:
        raise SheetTimeout
    return left


def check() -> None:
    """Stop the sheet when its time is up."""
    remaining()
