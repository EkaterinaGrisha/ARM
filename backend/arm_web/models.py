"""Report models re-exported from ``kdcheck.schemas`` while the pipeline moves to the core, plus the API job model.

New code imports the report contract from ``kdcheck.schemas``. ``Job`` belongs to the API, not to the report.
"""

from __future__ import annotations

from typing import Literal

from kdcheck.schemas import (
    SCHEMA_VERSION,
    CriticalParameter,
    Discrepancy,
    DiscrepancyType,
    DocumentInfo,
    DocumentKind,
    DocumentOrigin,
    DocumentStatus,
    ErrorCase,
    Extracted,
    ExtractionMethod,
    KitInfo,
    LogEntry,
    NodeKind,
    NodeStatus,
    PageInfo,
    Parameter,
    ParameterCategory,
    PartRecord,
    QualityMetric,
    QualityReport,
    Report,
    Severity,
    SourceRef,
    Specification,
    SpecRow,
    Summary,
    TitleBlock,
    TreeNode,
)
from pydantic import BaseModel, ConfigDict

__all__ = [
    "SCHEMA_VERSION",
    "CriticalParameter",
    "Discrepancy",
    "DiscrepancyType",
    "DocumentInfo",
    "DocumentKind",
    "DocumentOrigin",
    "DocumentStatus",
    "ErrorCase",
    "Extracted",
    "ExtractionMethod",
    "Job",
    "JobStatus",
    "KitInfo",
    "LogEntry",
    "NodeKind",
    "NodeStatus",
    "PageInfo",
    "Parameter",
    "ParameterCategory",
    "PartRecord",
    "QualityMetric",
    "QualityReport",
    "Report",
    "Severity",
    "SourceRef",
    "SpecRow",
    "Specification",
    "Summary",
    "TitleBlock",
    "TreeNode",
]

JobStatus = Literal["queued", "running", "done", "failed"]


class Job(BaseModel):
    """Состояние задания на обработку комплекта (API веб-сервиса)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    status: JobStatus = "queued"
    stage: str | None = None
    progress: float = 0.0
    message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    files_count: int = 0
    total_bytes: int = 0
    error: str | None = None
    summary: Summary | None = None
    root_designation: str | None = None
