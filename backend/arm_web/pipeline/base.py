"""Интерфейс конвейера обработки комплекта."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..models import Report

# (этап, доля выполнения 0..1, пояснение)
ProgressCallback = Callable[[str, float, str | None], None]


def no_progress(stage: str, progress: float, message: str | None = None) -> None:
    pass


class Pipeline(Protocol):
    name: str
    version: str

    def run(self, input_dir: Path, *, kit_id: str, kit_name: str, progress: ProgressCallback = no_progress) -> Report:
        """Обрабатывает папку с комплектом и возвращает отчёт."""
        ...
