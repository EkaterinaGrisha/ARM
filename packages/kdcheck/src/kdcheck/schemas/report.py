"""Report contract: the format of ``report.json`` for a checked doc set.

The contract between the pipeline (blocks 1 and 2), the API and the web UI. Any pipeline that
writes ``report.json`` in this format plugs into the web UI without frontend changes.

Class docstrings and field descriptions are published in the JSON Schema (``docs/report.schema.json``)
for integrators, so they stay in Russian like other user-facing strings.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.1"

ExtractionMethod = Literal["text_layer", "ocr", "vlm", "llm", "filename", "rule", "manual"]
DocumentKind = Literal["specification", "assembly_drawing", "part_drawing", "drawing", "other", "unknown"]
DocumentStatus = Literal["ok", "warning", "damaged", "unreadable", "foreign", "unsupported"]
DocumentOrigin = Literal["vector", "scan", "mixed", "unknown"]
NodeKind = Literal["kit", "assembly", "part", "standard", "other_item", "material", "document", "unknown"]
NodeStatus = Literal["ok", "missing", "warning", "not_required"]
DiscrepancyType = Literal[
    "missing_drawing",
    "orphan_drawing",
    "name_mismatch",
    "duplicate_designation",
    "revision_mismatch",
    "missing_specification",
]
Severity = Literal["high", "medium", "low"]
ParameterCategory = Literal[
    "dimension_tolerance",
    "general_tolerance",
    "roughness",
    "geometric",
    "heat_treatment",
    "hardness",
    "coating",
    "other",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class SourceRef(Model):
    """Откуда получено значение: документ, страница и область страницы."""

    document_id: str
    page: int = Field(1, description="Номер страницы, начиная с 1")
    bbox: tuple[float, float, float, float] | None = Field(
        None, description="Область [x0, y0, x1, y1] в долях ширины/высоты страницы, начало — левый верхний угол"
    )
    text: str | None = Field(None, description="Исходный распознанный текст")
    method: ExtractionMethod | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    engine: str | None = Field(None, description="Движок, прочитавший текст: pdfium, tesseract… С версии 1.1")
    engine_version: str | None = Field(None, description="Версия движка. С версии 1.1")


class Extracted(Model):
    """Извлечённое значение с прослеживаемостью."""

    value: str | float | int | None = None
    raw: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    source: SourceRef | None = None


class PageInfo(Model):
    number: int
    width: float
    height: float
    unit: Literal["pt", "px"] = "pt"
    has_text: bool = False


class DocumentInfo(Model):
    id: str
    file_name: str
    path: str = Field(description="Путь относительно корня комплекта")
    size_bytes: int = 0
    media_type: str | None = None
    sha256: str | None = None
    pages: list[PageInfo] = []
    kind: DocumentKind = "unknown"
    origin: DocumentOrigin = "unknown"
    status: DocumentStatus = "ok"
    problem: str | None = Field(None, description="Пояснение для проблемных файлов")
    designation: str | None = None
    name: str | None = None
    notes: list[str] = []


class TitleBlock(Model):
    """Поля основной надписи (ГОСТ 2.104)."""

    designation: Extracted | None = None
    name: Extracted | None = None
    material: Extracted | None = None
    mass: Extracted | None = None
    scale: Extracted | None = None
    litera: Extracted | None = None
    revision: Extracted | None = Field(None, description="Номер последнего изменения")
    archive_number: Extracted | None = None
    first_use: Extracted | None = Field(None, description="Первое применение")
    sheet: Extracted | None = None
    sheets: Extracted | None = None
    format: Extracted | None = None


class Parameter(Model):
    """Параметр технических требований в единой структуре."""

    id: str
    category: ParameterCategory
    name: str = Field(description="Короткое имя: Ra, Ø50k6, Твёрдость, Покрытие…")
    text: str = Field(description="Исходный текст требования")
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None
    qualifier: str | None = Field(None, description="Поле допуска (H7), шкала (HRC), базы, вид ТО…")
    it_grade: int | None = None
    general: bool = Field(False, description="Общее требование (неуказанные отклонения, шероховатость остального)")
    critical: bool = False
    source: SourceRef | None = None
    confidence: float | None = Field(None, ge=0, le=1)


class PartRecord(Model):
    """Извлечённые данные одного чертежа."""

    document_id: str
    designation: str | None = None
    name: str | None = None
    kind: DocumentKind = "part_drawing"
    title_block: TitleBlock = TitleBlock()
    requirements: list[Extracted] = Field([], description="Пункты технических требований")
    parameters: list[Parameter] = []


class SpecRow(Model):
    id: str
    section: str | None = None
    format: Extracted | None = None
    zone: Extracted | None = None
    position: Extracted | None = None
    designation: Extracted | None = None
    name: Extracted | None = None
    quantity: Extracted | None = None
    note: Extracted | None = None
    source: SourceRef | None = None


class Specification(Model):
    document_id: str
    designation: Extracted | None = None
    name: Extracted | None = None
    litera: Extracted | None = None
    revision: Extracted | None = None
    rows: list[SpecRow] = []


class TreeNode(Model):
    id: str
    designation: str | None = None
    name: str | None = None
    kind: NodeKind = "unknown"
    section: str | None = None
    position: str | None = None
    quantity: float | None = None
    total_quantity: float | None = Field(None, description="Количество на изделие верхнего уровня")
    spec_document_id: str | None = None
    drawing_document_ids: list[str] = []
    status: NodeStatus = "ok"
    discrepancy_ids: list[str] = []
    children: list[TreeNode] = []


class Discrepancy(Model):
    id: str
    type: DiscrepancyType
    severity: Severity = "medium"
    designation: str | None = None
    title: str
    message: str
    expected: str | None = None
    actual: str | None = None
    document_ids: list[str] = []
    tree_node_ids: list[str] = []
    sources: list[SourceRef] = []


class CriticalParameter(Model):
    id: str
    document_id: str
    designation: str | None = None
    part_name: str | None = None
    parameter_id: str | None = None
    category: ParameterCategory
    text: str
    rule: str = Field(description="Код правила отбора")
    reason: str = Field(description="Обоснование для технолога")
    score: float = Field(0.5, ge=0, le=1)
    source: SourceRef | None = None


class QualityMetric(Model):
    block: Literal["completeness", "extraction"]
    name: str
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    accuracy: float | None = None
    support: int | None = None


class ErrorCase(Model):
    block: Literal["completeness", "extraction"]
    kind: Literal["false_positive", "false_negative", "wrong_value"]
    title: str
    description: str = ""
    document_id: str | None = None


class QualityReport(Model):
    dataset: str = ""
    methodology: str = ""
    metrics: list[QualityMetric] = []
    errors: list[ErrorCase] = []
    limitations: list[str] = []


class LogEntry(Model):
    level: Literal["info", "warning", "error"] = "info"
    message: str
    document_id: str | None = None


class EngineInfo(Model):
    """Движок или библиотека, от которых зависит результат, и их версия. С версии 1.1."""

    name: str
    version: str | None = None


class KitInfo(Model):
    id: str
    name: str
    root_designation: str | None = None
    root_name: str | None = None
    created_at: str | None = None
    processed_at: str | None = None
    duration_sec: float | None = None
    pipeline_name: str | None = None
    pipeline_version: str | None = None
    engines: list[EngineInfo] = Field([], description="Движки и библиотеки с версиями. С версии 1.1")
    config_sha256: str | None = Field(
        None, description="SHA-256 настроек configs/*.yaml, с которыми получен отчёт. С версии 1.1"
    )


class Summary(Model):
    documents_total: int = 0
    documents_by_status: dict[str, int] = {}
    documents_by_kind: dict[str, int] = {}
    specifications: int = 0
    drawings: int = 0
    tree_items: int = 0
    parts: int = 0
    parameters: int = 0
    discrepancies_total: int = 0
    discrepancies_by_type: dict[str, int] = {}
    critical_parameters: int = 0
    completeness_ratio: float | None = Field(None, description="Доля позиций, требующих чертежа, для которых он найден")


class Report(Model):
    schema_version: str = SCHEMA_VERSION
    kit: KitInfo
    summary: Summary = Summary()
    documents: list[DocumentInfo] = []
    specifications: list[Specification] = []
    tree: list[TreeNode] = []
    discrepancies: list[Discrepancy] = []
    parts: list[PartRecord] = []
    critical_parameters: list[CriticalParameter] = []
    quality: QualityReport | None = None
    log: list[LogEntry] = []


def report_json_schema() -> str:
    """The report JSON Schema exactly as committed in ``docs/report.schema.json``."""
    return json.dumps(Report.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
