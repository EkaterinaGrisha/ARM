"""A team pipeline plugged in as an external program.

The command comes from ``ARM_PIPELINE_CMD`` with ``{input}`` and ``{output}`` placeholders, for example
``python -m team_pipeline --input {input} --output {output}``. The program writes ``{output}/report.json`` in the
:class:`kdcheck.schemas.Report` format and may update ``{output}/progress.json`` as
``{"stage": "...", "progress": 0.4, "message": "..."}``; the web UI then shows the progress.
"""

from __future__ import annotations

import shlex

from .process import ProcessPipeline


class ExternalPipeline(ProcessPipeline):
    def __init__(self, command: str, timeout_sec: int = 3600, memory_mb: int | None = None):
        super().__init__("external", "-", shlex.split(command), timeout_sec=timeout_sec, memory_mb=memory_mb)
