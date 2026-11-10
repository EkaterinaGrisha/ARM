"""Cells of a raster or a scan read by the PP-OCRv5 text line recognizer on the CPU (ADR-0024): ``kdcheck[ppocr]``
plus the files ``scripts/fetch_ppocr.sh`` puts in ``models/ppocr``, or in ``ARM_MODELS_DIR``.

The recognizer reads one line of text cut close to its ink: with the paper of the whole cell around a line it drops
characters even of a clean print. So the lines of a cell are found by the rows of ink of its binarized image, each is
cut from the grey image to the ink of its characters, specks far from them left out, and brought to the height the
model reads. The model gives, for each step along the line, the probability of each character of its alphabet; the
text is the most probable character of each step, repeats and blanks dropped (CTC). In a cell that may hold only some
characters, a Latin letter that looks like a Cyrillic one gives its probability to that letter, and the others are not
read at all, as Tesseract reads only them there. A word spans from the space before it to the space after it, and
its box is its ink there.

Without onnxruntime or the files, :func:`recognizer` gives None and the cells are read by Tesseract.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from kdcheck.config import OcrPpocr, models_dir
from kdcheck.ingest.text import Line, Word

log = logging.getLogger(__name__)

Place = Callable[[float, float], tuple[float, float]]  # a pixel of the cell image → a page point
Box = tuple[int, int, int, int]
Probs = NDArray[np.float32]  # steps × characters
GREY_HALF = 127.5  # the recognizer reads grey levels scaled to −1…1
PERCENT = 100  # min_word_confidence is 0–100, as Tesseract gives it
HASH_DIGITS = 12  # of the SHA-256 of the model file in the report


class Session(Protocol):
    """What :func:`recognizer` needs of ``onnxruntime.InferenceSession``."""

    def run(self, output_names: Sequence[str] | None, input_feed: Mapping[str, Any]) -> list[Any]: ...


@dataclass(frozen=True)
class Char:
    text: str
    probability: float
    step: int


@dataclass(frozen=True)
class Recognizer:
    """A recognizer with its alphabet: ``alphabet[0]`` is the blank of CTC."""

    session: Session
    alphabet: tuple[str, ...]
    name: str
    version: str
    input: str = "x"

    def probabilities(self, line: Image.Image, height: int) -> Probs:
        """The probability of each character at each step along ``line``, one line of text."""
        width = max(1, math.ceil(height * line.width / line.height))
        grey = np.asarray(line.convert("L").resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)
        channels = np.repeat((grey / GREY_HALF - 1)[None, None], 3, axis=1)  # the same grey in each channel
        return np.asarray(self.session.run(None, {self.input: channels})[0][0], dtype=np.float32)


Allowed = tuple[NDArray[np.bool_], tuple[tuple[int, int], ...]]


@functools.cache
def allowed(alphabet: tuple[str, ...], characters: str, lookalikes: tuple[tuple[str, str], ...]) -> Allowed:
    """The characters of ``alphabet`` a cell of ``characters`` may hold, the blank and the space among them, and the
    pairs (Latin lookalike, its Cyrillic letter) of the alphabet whose probabilities add up."""
    keep = {*characters, " "}
    mask = np.array([i == 0 or ch in keep for i, ch in enumerate(alphabet)])
    index = {ch: i for i, ch in enumerate(alphabet) if i}
    pairs = tuple((index[a], index[c]) for a, c in lookalikes if a in index and c in index and c in keep)
    return mask, pairs


def decode(probs: Probs, alphabet: Sequence[str], limit: Allowed | None = None) -> list[Char]:
    """The characters of a line: the most probable one of each step, repeats and blanks dropped. ``limit`` keeps
    them to the characters of a cell, a Latin lookalike adding its probability to its Cyrillic letter first."""
    if limit is not None:
        mask, pairs = limit
        probs = probs.copy()
        for latin, cyrillic in pairs:
            probs[:, cyrillic] += probs[:, latin]
        probs = np.where(mask[None, :], probs, 0)
    best = probs.argmax(axis=1).tolist()
    chars, previous = [], 0
    for step, k in enumerate(best):
        if k != previous and k != 0:
            chars.append(Char(alphabet[k], float(probs[step, k]), step))
        previous = k
    return chars


def line_boxes(ink: NDArray[np.bool_], rules: OcrPpocr, low: float, min_ink: float) -> list[Box]:
    """The lines of text of a cell whose ink is ``ink``, each cut close to its characters with ``rules.margin`` of
    paper. The core of a line is a run of rows with more ink than ``rules.valley`` of the median row with ink, at least
    ``low`` px high, and the rows of ink that touch it are its line too: ascenders and descenders. Two lines part at
    the row with the least ink between their cores, so the lines of a name in two lines part though a descender of one
    touches the other. Ink
    farther than ``rules.speck_gap`` line heights from the rest of its line with less than ``min_ink`` px is a speck."""
    counts = ink.sum(axis=1)
    if not counts.any():
        return []
    level = rules.valley * float(np.median(counts[counts > 0]))
    cores = [(a, b) for a, b in _runs(counts > level) if b - a >= low]
    if not cores:
        return []
    parts = [a + int(np.argmin(counts[a:b])) for (_, a), (b, _) in zip(cores, cores[1:], strict=False)]
    lines = []
    for (top, bottom), start, end in zip(cores, [0, *parts], [*parts, len(counts)], strict=True):
        while top > start and counts[top - 1]:  # the rows of ink that touch the core: ascenders and descenders
            top -= 1
        while bottom < end and counts[bottom]:
            bottom += 1
        lines.append((top, bottom))
    boxes = []
    height, width = ink.shape
    for top, bottom in lines:
        h = bottom - top
        if h < low:
            continue
        band = ink[top:bottom]
        groups: list[list[int]] = []  # x0, x1, ink
        for a, b in _runs(np.any(band, axis=0)):
            if groups and a - groups[-1][1] < rules.speck_gap * h:
                groups[-1][1] = b
                groups[-1][2] += int(band[:, a:b].sum())
            else:
                groups.append([a, b, int(band[:, a:b].sum())])
        kept = [g for g in groups if g[2] >= min_ink]
        if not kept:
            continue
        m = round(rules.margin * h)
        x0, x1 = min(g[0] for g in kept), max(g[1] for g in kept)
        boxes.append((max(0, x0 - m), max(0, top - m), min(width, x1 + m), min(height, bottom + m)))
    return boxes


def _runs(values: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """Starts and ends of the runs of True."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], values.astype(np.int8), [0]))))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def read(
    recognizer: Recognizer,
    grey: Image.Image,
    ink: Image.Image,
    place: Place,
    rules: OcrPpocr,
    *,
    low: float,
    min_ink: float,
    min_confidence: float,
    characters: str | None = None,
    lookalikes: Mapping[str, str] | None = None,
) -> list[Line]:
    """The lines of words of a cell: ``grey`` is its image, ``ink`` the mask of its ink (white on black) of the same
    size; ``place`` takes a pixel to the page. ``low`` and ``min_ink`` are in pixels of the image, ``min_confidence``
    is 0–100 as for Tesseract."""
    mask = np.asarray(ink.convert("L")) > 0
    limit = allowed(recognizer.alphabet, characters, tuple((lookalikes or {}).items())) if characters else None
    lines = []
    for box in line_boxes(mask, rules, low, min_ink) or [(0, 0, grey.width, grey.height)]:
        crop = grey.crop(box)
        probs = recognizer.probabilities(crop, rules.height)
        step = crop.width / max(len(probs), 1)  # px of the crop each step of the answer covers
        band = mask[box[1] : box[3], box[0] : box[2]]
        words = [
            w
            for w in _words(decode(probs, recognizer.alphabet, limit), box, step, band, place, recognizer)
            if w[1] * PERCENT >= min_confidence
        ]
        if not words:
            continue
        found = [w for w, _ in words]
        bbox = (
            min(w.bbox[0] for w in found),
            min(w.bbox[1] for w in found),
            max(w.bbox[2] for w in found),
            max(w.bbox[3] for w in found),
        )
        lines.append(
            Line(
                text=" ".join(w.text for w in found),
                bbox=bbox,
                size=bbox[3] - bbox[1],
                words=found,
                confidence=round(sum(c for _, c in words) / len(words), 2),
                engine=(recognizer.name, recognizer.version),
            )
        )
    return lines


def _words(
    chars: list[Char], box: Box, step: float, band: NDArray[np.bool_], place: Place, recognizer: Recognizer
) -> list[tuple[Word, float]]:
    """The words of a line, split at spaces, with the mean probability of their characters. A word spans from the
    space before it to the space after it, the middle of their steps, and its box is its ink there: the step the model
    gives a character lies near its ink, not on it."""
    found: list[tuple[list[Char], float, float]] = []  # characters, left and right edge in px of the line
    current: list[Char] = []
    left = 0.0
    for ch in chars:
        if not ch.text.isspace():
            current.append(ch)
            continue
        edge = (ch.step + 1 / 2) * step
        if current:
            found.append((current, left, edge))
        current, left = [], edge
    if current:
        found.append((current, left, float(band.shape[1])))
    words = []
    for group, a, b in found:
        x0, x1 = round(a), max(round(a) + 1, round(b))
        y0, y1 = 0, band.shape[0]
        columns = np.flatnonzero(np.any(band[:, x0:x1], axis=0))
        if len(columns):
            x0, x1 = x0 + int(columns[0]), x0 + int(columns[-1]) + 1
            rows = np.flatnonzero(np.any(band[:, x0:x1], axis=1))
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
        corners = [place(box[0] + x, box[1] + y) for x in (x0, x1) for y in (y0, y1)]
        bbox = (
            min(c[0] for c in corners),
            min(c[1] for c in corners),
            max(c[0] for c in corners),
            max(c[1] for c in corners),
        )
        text = "".join(ch.text for ch in group)
        confidence = sum(ch.probability for ch in group) / len(group)
        words.append((Word(text, bbox, engine=(recognizer.name, recognizer.version)), confidence))
    return words


def recognizer(rules: OcrPpocr) -> Recognizer | None:
    """The recognizer of ``rules`` from ``models_dir()/ppocr``; None, with a warning once, without onnxruntime or its
    files."""
    return _load(str(models_dir() / "ppocr"), rules.model)


@functools.cache
def _load(folder: str, model: str) -> Recognizer | None:
    onnx, about = Path(folder) / f"{model}.onnx", Path(folder) / f"{model}.json"
    try:
        import onnxruntime
    except ImportError:
        log.warning("Нет onnxruntime (kdcheck[ppocr]): ячейки читает Tesseract")
        return None
    if not onnx.is_file() or not about.is_file():
        log.warning("Нет файлов %s в %s (scripts/fetch_ppocr.sh): ячейки читает Tesseract", model, folder)
        return None
    alphabet = json.loads(about.read_text(encoding="utf-8"))["alphabet"]
    digest = hashlib.sha256(onnx.read_bytes()).hexdigest()
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3  # errors only: the converted model has initializers it does not use
    session = onnxruntime.InferenceSession(str(onnx), options, providers=["CPUExecutionProvider"])
    version = f"onnx sha256:{digest[:HASH_DIGITS]}, onnxruntime {onnxruntime.__version__}"
    return Recognizer(session, ("", *alphabet, " "), model, version, session.get_inputs()[0].name)
