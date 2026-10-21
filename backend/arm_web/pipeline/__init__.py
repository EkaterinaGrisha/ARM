"""Pipeline choice: the core in a separate process (default), an external team program, or in-process threads."""

from __future__ import annotations

import os
import sys

from .base import Pipeline, ProgressCallback, no_progress


def get_pipeline() -> Pipeline:
    timeout = int(os.environ.get("ARM_PIPELINE_TIMEOUT", "3600"))
    memory = int(os.environ.get("ARM_JOB_MEMORY_MB", "8192")) or None
    use_ocr = os.environ.get("ARM_OCR", "1") != "0"
    command = os.environ.get("ARM_PIPELINE_CMD", "").strip()
    if command:
        from .external import ExternalPipeline

        return ExternalPipeline(command, timeout_sec=timeout, memory_mb=memory)
    from kdcheck.run import BaselinePipeline

    if os.environ.get("ARM_INPROCESS") == "1":  # rollback to the prototype: the core runs in API threads, no limits
        return BaselinePipeline(use_ocr=use_ocr)
    from .process import ProcessPipeline

    args = [sys.executable, "-m", "kdcheck", "run", "{input}", "-o", "{output}"]
    args += ["--kit-id", "{kit_id}", "--name", "{kit_name}", "--progress-file", "{output}/progress.json"]
    if not use_ocr:
        args.append("--no-ocr")
    return ProcessPipeline(BaselinePipeline.name, BaselinePipeline.version, args, timeout_sec=timeout, memory_mb=memory)


__all__ = ["Pipeline", "ProgressCallback", "get_pipeline", "no_progress"]
