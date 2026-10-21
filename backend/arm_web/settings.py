"""Settings of the web service: limits from ``configs/api.yaml``, overridden by ``ARM_*`` variables (ADR-0012).

The settings directory is ``ARM_CONFIG_DIR``, else ``configs/`` of the repository the service runs from. The service
passes it to every ``kdcheck run`` process, so the API and the core read the same files. Values are read on each
call: a changed variable or file applies to the next request.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO = Path(__file__).resolve().parents[2]

# api.yaml key → the environment variable that overrides it
OVERRIDES = {
    "max_upload_mb": "ARM_MAX_UPLOAD_MB",
    "max_files": "ARM_MAX_FILES",
    "job_timeout_sec": "ARM_PIPELINE_TIMEOUT",
    "job_memory_mb": "ARM_JOB_MEMORY_MB",
    "workers": "ARM_WORKERS",
}


class ApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_upload_mb: int = Field(gt=0)
    max_files: int = Field(gt=0)
    job_timeout_sec: int = Field(gt=0)
    job_memory_mb: int = Field(ge=0, description="0: no limit")
    workers: int = Field(gt=0)
    progress_poll_sec: float = Field(gt=0)
    page_image_px: int = Field(gt=0)


def config_dir() -> Path:
    return Path(os.environ.get("ARM_CONFIG_DIR") or REPO / "configs")


def api_settings() -> ApiSettings:
    path = config_dir() / "api.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Настройки API не читаются: {path}: {exc}") from None
    if isinstance(raw, dict):
        raw.update({key: os.environ[var] for key, var in OVERRIDES.items() if os.environ.get(var)})
    try:
        return ApiSettings.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(f"Настройки API не прошли проверку: {path}\n{exc}") from None
