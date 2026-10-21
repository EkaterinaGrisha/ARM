"""A pipeline that runs as a separate process with a time and memory limit (ADR-0006).

The process gets the doc set folder and an output folder. It writes ``{output}/report.json`` in the report format
and may keep ``{output}/progress.json`` as ``{"stage": "...", "progress": 0.4, "message": "..."}`` up to date.
A hung or memory-hungry file then ends one job, not the server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..models import Report
from .base import ProgressCallback, no_progress

PLACEHOLDER = re.compile(r"\{(input|output|kit_id|kit_name)\}")


class ProcessPipeline:
    def __init__(
        self,
        name: str,
        version: str,
        command: list[str],
        *,
        timeout_sec: int,
        memory_mb: int | None = None,
        poll_sec: float,
    ):
        """``command`` items may contain ``{input}``, ``{output}``, ``{kit_id}`` and ``{kit_name}``.

        Limits come from ``configs/api.yaml``: the process is checked every ``poll_sec`` seconds.
        """
        self.name = name
        self.version = version
        self.command = command
        self.timeout_sec = timeout_sec
        self.memory_mb = memory_mb
        self.poll_sec = poll_sec

    def run(self, input_dir: Path, *, kit_id: str, kit_name: str, progress: ProgressCallback = no_progress) -> Report:
        output_dir = input_dir.parent / "pipeline_output"
        shutil.rmtree(output_dir, ignore_errors=True)  # a rerun must not pick up the previous report
        output_dir.mkdir(parents=True)
        values = {"input": str(input_dir), "output": str(output_dir), "kit_id": kit_id, "kit_name": kit_name}
        # Only the known placeholders are replaced: other braces, such as JSON in an argument, stay as they are.
        args = [PLACEHOLDER.sub(lambda m: values[m.group(1)], a) for a in self.command]
        if self.memory_mb:
            args = [sys.executable, "-m", "arm_web.sandbox", "--memory-mb", str(self.memory_mb), "--", *args]
        log_path = output_dir / "pipeline.log"
        progress("Запуск конвейера", 0.0, None)
        started = time.monotonic()
        with log_path.open("wb") as log:
            # A session of its own, so a timeout stops the whole group, OCR subprocesses included.
            proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=output_dir, start_new_session=True)
            try:
                while proc.poll() is None:
                    if time.monotonic() - started > self.timeout_sec:
                        raise RuntimeError(f"Превышено время обработки ({self.timeout_sec} с)")
                    self._report_progress(output_dir / "progress.json", progress)
                    time.sleep(self.poll_sec)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        if proc.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
            if self.memory_mb and "MemoryError" in tail:
                raise RuntimeError(f"Превышен лимит памяти ({self.memory_mb} МБ)")
            raise RuntimeError(f"Конвейер завершился с кодом {proc.returncode}:\n{tail}")
        report_path = output_dir / "report.json"
        if not report_path.exists():
            raise RuntimeError("Конвейер не записал report.json")
        report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        report.kit.id = kit_id
        report.kit.name = report.kit.name or kit_name
        report.kit.duration_sec = report.kit.duration_sec or round(time.monotonic() - started, 2)
        report.kit.pipeline_name = report.kit.pipeline_name or self.name
        return report

    @staticmethod
    def _report_progress(path: Path, progress: ProgressCallback) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            progress(str(data.get("stage") or "Обработка"), float(data.get("progress") or 0), data.get("message"))
        except (OSError, ValueError, TypeError):
            pass
