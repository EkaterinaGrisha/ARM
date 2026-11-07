"""Running the core: the public entry point for the API, the CLI and other callers (ADR-0011)."""

from __future__ import annotations

import logging
from pathlib import Path

from kdcheck import cache as read_cache
from kdcheck.config import ConfigError, Settings, config_dir, load_settings
from kdcheck.ocr import tesseract
from kdcheck.pipeline import BaselinePipeline, ProgressCallback, no_progress
from kdcheck.schemas import Report

log = logging.getLogger(__name__)

__all__ = [
    "BaselinePipeline",
    "ConfigError",
    "ProgressCallback",
    "Settings",
    "config_dir",
    "load_settings",
    "no_progress",
    "ocr_available",
    "run",
]


def run(
    input_dir: Path,
    *,
    kit_id: str,
    kit_name: str,
    config: Path | None = None,
    use_ocr: bool | None = None,
    max_pages: int | None = None,
    use_vlm: bool | None = None,
    progress: ProgressCallback = no_progress,
    cache: Path | None = None,
) -> Report:
    """Process a doc set folder with the baseline pipeline and the settings in ``config`` (ADR-0012).

    ``use_ocr``, ``max_pages`` and ``use_vlm`` override the settings when given; ``max_pages=0`` reads every page.
    ``cache`` keeps the answers of OCR and of the model in that folder and gives them back on the next run (ADR-0018),
    by default the folder of ``ARM_CACHE_DIR``; without either nothing is kept.
    """
    settings = load_settings(config, use_ocr=use_ocr, max_pages=max_pages, use_vlm=use_vlm)
    folder = cache or read_cache.folder_from_env()
    with read_cache.use(folder):
        report = BaselinePipeline(settings).run(input_dir, kit_id=kit_id, kit_name=kit_name, progress=progress)
    if folder is not None:
        log.info("Кэш прочтений %s — %s", folder, read_cache.summary() or "обращений не было")
    return report


def ocr_available(settings: Settings | None = None) -> bool:
    """Whether scans get recognized: OCR is on in the settings and Tesseract has their languages."""
    ocr = (settings or load_settings()).models.ocr
    return ocr.enabled and tesseract.available(ocr.languages)
