"""Running the core: the public entry point for the API, the CLI and other callers (ADR-0011)."""

from __future__ import annotations

from pathlib import Path

from kdcheck.ocr import tesseract
from kdcheck.pipeline import BaselinePipeline, ProgressCallback, no_progress
from kdcheck.schemas import Report

__all__ = ["BaselinePipeline", "ProgressCallback", "no_progress", "ocr_available", "run"]


def run(
    input_dir: Path, *, kit_id: str, kit_name: str, use_ocr: bool = True, progress: ProgressCallback = no_progress
) -> Report:
    """Process a doc set folder with the baseline pipeline."""
    return BaselinePipeline(use_ocr=use_ocr).run(input_dir, kit_id=kit_id, kit_name=kit_name, progress=progress)


def ocr_available() -> bool:
    """Whether scans get recognized: Tesseract with Russian is installed and ARM_OCR is not 0."""
    return tesseract.available()
