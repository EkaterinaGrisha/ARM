"""Scans of rendered sheets through the default pipeline of Augraphy: the second source of degradations (ADR-0013).

The entry is that of :func:`synth.degrade.capture`, so a kit takes either. The scanner settings — resolution, pixels,
compression — are drawn from the class of quality with the seed of the document as :mod:`synth.degrade` draws them,
so a document of a class gets the same settings from both. What happens on paper and on the glass is the default
pipeline of Augraphy as its authors made it: the stress corpus judges our module by someone else's idea of a scan, not
by our ranges in another library. A raster has no paper copy and an unreadable scan is the problem file of a
mutation: both stay with :mod:`synth.degrade`.

Augraphy 8.2.6 repeats by seed once everything it draws from is fixed:

- the generators of Python, numpy and OpenCV, and those of numba, which its compiled functions draw from and which it
  keeps per thread: numba runs on one thread during a capture, seeded in the calling thread and in its one worker;
- the files it reads from the working folder: bleed-through, page borders and book binding take a sheet from the
  image cache the pipeline writes there, and scribbles take a font from it, which Augraphy downloads from the
  internet when there is none. A capture runs in a folder of its own, with the pages of the document in the cache
  and DejaVu Sans as the font.

``AugraphyPipeline.augment`` cannot carry points through the choices the default pipeline is made of: ``OneOf`` and
``AugmentationSequence`` return two values where it unpacks four. So the phases are walked here with the same gates,
choices and order. The corners of every box of the truth go through each effect as key points, and the record lists
the effects that ran, sheet by sheet; the log of Augraphy names every member of a sequence that ran, whether the
member itself ran or not.

Augraphy comes with the ``augraphy`` group of ``synth``, on Python 3.11 only: ``uv run --group augraphy …``.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.metadata
import random
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numba
import numpy as np
import pypdfium2 as pdfium
from augraphy import AugmentationSequence, OneOf, default_augraphy_pipeline
from PIL import Image
from pydantic import JsonValue

from synth import degrade
from synth.model import Output
from synth.render import Box
from synth.truth import AugraphyCapture, AugraphySheet, CaptureRecord, Effect

FONT = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"  # what scribbles write in, in place of a download
DATA = frozenset({"mask", "keypoints", "bounding_boxes", "numba_jit"})  # attributes of an effect that are no settings
Points = dict[str, list[list[float]]]  # key points by the key of a box: its corners, in pixels
_SKIP = object()  # a value JSON does not hold: an image, an object


def capture(pdf: bytes, output: Output, seed: int, boxes: dict[str, Box] | None = None) -> tuple[bytes, CaptureRecord]:
    """The file of a document scanned through Augraphy as ``output`` asks, and how; ``boxes`` move with their sheets.

    A raster and an unreadable scan go to :func:`synth.degrade.capture`. A box whose corners all leave the scan is
    dropped: its value is no longer on the sheet.
    """
    if output.kind != "scan" or output.quality == "unreadable":
        return degrade.capture(pdf, output, seed, boxes)
    document = pdfium.PdfDocument(pdf)
    if output.container in ("jpg", "png") and len(document) > 1:
        raise ValueError(f"{output.container} holds one page, the document has {len(document)}")
    sizes = [(w / 72 * degrade.MM_PER_INCH, h / 72 * degrade.MM_PER_INCH) for w, h in (p.get_size() for p in document)]
    scanner = degrade.plan(output, sizes, seed, random.Random(seed))  # only its resolution, pixels and compression
    pages = [page.render(scale=scanner.dpi / 72, grayscale=True).to_pil().convert("L") for page in document]
    drawn = boxes if boxes is not None else {}
    images: list[Image.Image] = []
    sheets: list[AugraphySheet] = []
    with _seeded(seed), _folder():
        pipeline = default_augraphy_pipeline()
        for number, page in enumerate(pages, start=1):
            sheet = np.repeat(np.asarray(page)[:, :, None], 3, axis=2)  # BGR, as Augraphy takes it
            cv2.imwrite(f"augraphy_cache/image_{number - 1}.png", sheet)  # as AugraphyPipeline.augment caches it
            keys = [key for key, (on, _) in drawn.items() if on == number]
            result, points, effects = _run(pipeline, sheet, {key: _corners(drawn[key][1], page.size) for key in keys})
            image, threshold = _cut(result, scanner.mode)
            images.append(image)
            sheets.append(AugraphySheet(effects=effects, pixels=image.size, threshold=threshold))
            for key in keys:
                if (moved := _box(points.get(key, []), image.size)) is None:
                    del drawn[key]
                else:
                    drawn[key] = (number, moved)
    record = AugraphyCapture.model_validate(
        {
            "quality": output.quality,
            "seed": seed,
            "dpi": scanner.dpi,
            "mode": scanner.mode,
            "container": output.container,
            "jpeg": scanner.jpeg,
            "version": importlib.metadata.version("augraphy"),
            "sheets": sheets,
        }
    )
    return degrade.encode(images, record), record


# ---------------------------------------------------------------------------------------- the pipeline


def _run(pipeline: Any, sheet: np.ndarray, points: Points) -> tuple[np.ndarray, Points, list[Effect]]:
    """A sheet through the phases of ``pipeline`` as ``AugraphyPipeline.augment`` takes it: the ink phase on the sheet,
    the paper phase on blank paper of its colour, the ink printed on the paper, the post phase on the print. The points
    follow the ink, then the print."""
    effects: list[Effect] = []

    def apply(phase: str, effect: Any, image: np.ndarray, points: Points, forced: bool) -> tuple[np.ndarray, Points]:
        if not forced and not effect.should_run():
            return image, points
        if isinstance(effect, OneOf):  # its choice was drawn with the pipeline, and it forces the one chosen
            chosen = effect.augmentations[int(np.argmax(effect.augmentation_probabilities))]
            return apply(phase, chosen, image, points, True)
        if isinstance(effect, AugmentationSequence):  # each member draws its own gate
            for member in effect.augmentations:
                image, points = apply(phase, member, image, points, False)
            return image, points
        image, _, points, _ = effect(image, layer=phase, keypoints=copy.deepcopy(points), force=True)
        effects.append(Effect(phase=phase, name=type(effect).__name__, settings=_settings(effect)))
        return image, points

    def walk(phase: str, sequence: Any, image: np.ndarray, points: Points) -> tuple[np.ndarray, Points]:
        for effect in sequence.augmentations:  # the gate of each, then the effect forced, as apply_phase runs them
            if effect.should_run():
                image, points = apply(phase, effect, image, points, True)
        return image, points

    ink, points = walk("ink", pipeline.ink_phase, sheet, points)
    low, high = pipeline.paper_color_range
    colour = random.randint(low, high) if (low, high) != (0, 0) else 255
    paper, _ = walk("paper", pipeline.paper_phase, np.full((*ink.shape[:2], 3), colour, np.uint8), {})
    printed = pipeline.print_ink_to_paper({"log": {}}, ink.copy(), paper.copy())
    result, points = walk("post", pipeline.post_phase, printed, points)
    return result, points, effects


def _settings(effect: Any) -> dict[str, JsonValue]:
    """The settings of an effect as JSON: numbers, text and lists of them, by name."""
    settings: dict[str, JsonValue] = {}
    for name, value in sorted(vars(effect).items()):
        if name not in DATA and (plain := _plain(value)) is not _SKIP:
            settings[name] = plain
    return settings


def _plain(value: Any) -> Any:
    """``value`` as JSON holds it, or ``_SKIP``."""
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple):
        items = [_plain(v) for v in value]
        return _SKIP if any(v is _SKIP for v in items) else items
    if isinstance(value, dict):
        named = {str(k): _plain(v) for k, v in value.items()}
        return _SKIP if any(v is _SKIP for v in named.values()) else named
    return _SKIP


@numba.njit
def _seed_numba(seed: int) -> None:
    """Seed the generators numba keeps for the calling thread, of ``random`` and of ``numpy.random``."""
    np.random.seed(seed)
    random.seed(seed)


@numba.njit(parallel=True)
def _seed_numba_worker(seed: int) -> None:
    """Seed them in the worker that runs a parallel loop: the only one while numba runs on one thread."""
    for _ in numba.prange(1):
        np.random.seed(seed)
        random.seed(seed)


@contextlib.contextmanager
def _seeded(seed: int) -> Iterator[None]:
    """Every generator Augraphy draws from seeded with ``seed``, numba on one thread; then all as they were."""
    state, numpy_state, threads = random.getstate(), np.random.get_state(), numba.get_num_threads()
    numba.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed)
    cv2.setRNGSeed(seed & 0x7FFFFFFF)  # a C int
    _seed_numba(seed)
    _seed_numba_worker(seed)
    try:
        yield
    finally:
        random.setstate(state)
        np.random.set_state(numpy_state)
        numba.set_num_threads(threads)


@contextlib.contextmanager
def _folder() -> Iterator[None]:
    """A working folder of its own: Augraphy caches its sheets there and reads the font of scribbles from it."""
    with tempfile.TemporaryDirectory(prefix="augraphy-") as work, contextlib.chdir(work):
        Path("augraphy_cache").mkdir()
        Path("fonts").mkdir()
        shutil.copy(FONT, Path("fonts") / FONT.name)
        yield


# ---------------------------------------------------------------------------------------- boxes and pixels


def _corners(box: degrade.Rect, size: tuple[int, int]) -> list[list[float]]:
    x0, y0, x1, y1 = box
    w, h = size
    return [[x0 * w, y0 * h], [x1 * w, y0 * h], [x0 * w, y1 * h], [x1 * w, y1 * h]]


def _box(corners: list[list[float]], size: tuple[int, int]) -> degrade.Rect | None:
    """The box that holds the corners left on the scan, in shares of it; none when the effects took them all off."""
    if not corners:
        return None
    w, h = size
    xs, ys = [float(p[0]) / w for p in corners], [float(p[1]) / h for p in corners]
    if max(xs) <= 0 or min(xs) >= 1 or max(ys) <= 0 or min(ys) >= 1:
        return None
    return (
        round(max(0.0, min(xs)), 4),
        round(max(0.0, min(ys)), 4),
        round(min(1.0, max(xs)), 4),
        round(min(1.0, max(ys)), 4),
    )


def _cut(result: np.ndarray, mode: str) -> tuple[Image.Image, int | None]:
    """The pixels the file keeps, and the threshold of a black and white file: by Otsu, as a scanner sets it for a
    sheet it has not seen."""
    image = Image.fromarray(np.ascontiguousarray(result if result.ndim == 2 else result[:, :, 2::-1]))  # BGR to RGB
    if mode == "colour":
        return image.convert("RGB"), None
    grey = image.convert("L")
    if mode == "grey":
        return grey, None
    level, _ = cv2.threshold(np.asarray(grey), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold = round(level)
    return grey.point(lambda v: 255 if v > threshold else 0, mode="1"), threshold
