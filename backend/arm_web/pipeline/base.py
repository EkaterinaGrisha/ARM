"""Интерфейс конвейера обработки комплекта."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kdcheck.pipeline import ProgressCallback, no_progress

from ..models import Report

__all__ = ["Pipeline", "ProgressCallback", "no_progress"]


class Pipeline(Protocol):
    name: str
    version: str

    def run(self, input_dir: Path, *, kit_id: str, kit_name: str, progress: ProgressCallback = no_progress) -> Report:
        """Обрабатывает папку с комплектом и возвращает отчёт."""
        ...
