"""Выбор конвейера обработки: встроенный базовый или внешняя программа команды."""

from __future__ import annotations

import os

from .base import Pipeline, ProgressCallback, no_progress


def get_pipeline() -> Pipeline:
    command = os.environ.get("ARM_PIPELINE_CMD", "").strip()
    if command:
        from .external import ExternalPipeline

        return ExternalPipeline(command, timeout_sec=int(os.environ.get("ARM_PIPELINE_TIMEOUT", "3600")))
    from .baseline.pipeline import BaselinePipeline

    return BaselinePipeline(use_ocr=os.environ.get("ARM_OCR", "1") != "0")


__all__ = ["Pipeline", "ProgressCallback", "get_pipeline", "no_progress"]
