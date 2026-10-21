"""Pipeline choice: the core in a separate process (default), an external team program, or in-process threads."""

from __future__ import annotations

import os
import sys

from ..settings import api_settings, config_dir
from .base import Pipeline, ProgressCallback, no_progress


def get_pipeline() -> Pipeline:
    api = api_settings()
    timeout, memory, poll = api.job_timeout_sec, api.job_memory_mb or None, api.progress_poll_sec
    command = os.environ.get("ARM_PIPELINE_CMD", "").strip()
    if command:
        from .external import ExternalPipeline

        return ExternalPipeline(command, timeout_sec=timeout, memory_mb=memory, poll_sec=poll)
    from kdcheck.run import BaselinePipeline, load_settings

    if os.environ.get("ARM_INPROCESS") == "1":  # rollback to the prototype: the core runs in API threads, no limits
        return BaselinePipeline(load_settings(config_dir()))
    from .process import ProcessPipeline

    # The job process inherits the environment, so ARM_OCR, ARM_PDF_BACKEND and ARM_MAX_PAGES apply to it as well.
    args = [sys.executable, "-m", "kdcheck", "run", "{input}", "-o", "{output}", "--config", str(config_dir())]
    args += ["--kit-id", "{kit_id}", "--name", "{kit_name}", "--progress-file", "{output}/progress.json"]
    return ProcessPipeline(
        BaselinePipeline.name, BaselinePipeline.version, args, timeout_sec=timeout, memory_mb=memory, poll_sec=poll
    )


__all__ = ["Pipeline", "ProgressCallback", "get_pipeline", "no_progress"]
