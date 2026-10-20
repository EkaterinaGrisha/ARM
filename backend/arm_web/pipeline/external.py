"""Подключение конвейера команды как внешней программы.

Команда задаётся переменной ``ARM_PIPELINE_CMD`` с подстановками ``{input}`` и ``{output}``,
например ``python -m team_pipeline --input {input} --output {output}``. Программа должна
записать ``{output}/report.json`` в формате :class:`arm_web.models.Report`; по желанию —
обновлять ``{output}/progress.json`` вида ``{"stage": "...", "progress": 0.4, "message": "..."}``,
тогда веб-интерфейс покажет ход обработки.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path

from ..models import Report
from .base import ProgressCallback, no_progress


class ExternalPipeline:
    name = "external"
    version = "-"

    def __init__(self, command: str, timeout_sec: int = 3600):
        self.command = command
        self.timeout_sec = timeout_sec

    def run(self, input_dir: Path, *, kit_id: str, kit_name: str, progress: ProgressCallback = no_progress) -> Report:
        output_dir = input_dir.parent / "pipeline_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        args = [a.format(input=str(input_dir), output=str(output_dir)) for a in shlex.split(self.command)]
        log_path = output_dir / "pipeline.log"
        progress("Запуск конвейера", 0.0, " ".join(args[:3]))
        started = time.monotonic()
        with log_path.open("wb") as log:
            proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=output_dir)
            while proc.poll() is None:
                if time.monotonic() - started > self.timeout_sec:
                    proc.kill()
                    raise RuntimeError(f"Превышено время обработки ({self.timeout_sec} с)")
                self._report_progress(output_dir / "progress.json", progress)
                time.sleep(1)
        if proc.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
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
