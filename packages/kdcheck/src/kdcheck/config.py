"""Settings of the core: GOST form templates, thresholds, dictionaries and engines from ``configs/*.yaml`` (ADR-0012).

Each file is one section of :class:`Settings`. The files are checked against these models, so an unknown or missing
key or a wrong value stops the run with the file and the field named. The directory comes from ``kdcheck run
--config``, else from ``ARM_CONFIG_DIR``, else it is ``configs/`` of the repository checkout. ``ARM_OCR``,
``ARM_PDF_BACKEND``, ``ARM_MAX_PAGES``, ``ARM_VLM``, ``ARM_VLM_URL`` and ``ARM_VLM_MODEL`` override single values.
The hash of the settings in effect goes into the report, so a result can be traced to the settings that gave it.
Prompts of the models are versioned files in ``prompts/`` of the checkout, or in ``ARM_PROMPTS_DIR``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kdcheck.schemas import ExtractionMethod, NodeKind, Severity

CONFIG_DIR_ENV = "ARM_CONFIG_DIR"
PROMPTS_DIR_ENV = "ARM_PROMPTS_DIR"
# Addresses of this machine: a model server there keeps the customer's documents on it (ADR-0014).
LOOPBACK = ("localhost", "127.0.0.1", "::1")

Box = tuple[float, float, float, float]  # x0, y0, x1, y1
Point = tuple[float, float]
Span = tuple[float, float]
Share = Annotated[float, Field(ge=0, le=1)]


class ConfigError(Exception):
    """Settings are missing or invalid: the installation is broken, so the run stops."""


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- forms.yaml


class Sheet(Section):
    formats: dict[str, Point]
    format_tolerance: Share
    frame_margin: float
    binding_margin: float
    general_roughness_zone: Point  # from the top and from the right edge


class TitleBlockCells(Section):
    designation: Box
    name: Box
    material: Box | None = None
    litera: Box
    mass: Box | None = None
    scale: Box | None = None
    sheet: Box
    sheets: Box
    changes: Box  # the «Изм.» column over the entries of the change record table


class RuledForm(Section):
    """A form by its ruled lines, which place it on a page image."""

    height: float
    lines: tuple[Box, ...]  # main ruled lines, horizontal or vertical
    blank: tuple[Box, ...]  # where the other forms have a line and this one has none

    @field_validator("lines", "blank")
    @classmethod
    def _straight(cls, value: tuple[Box, ...]) -> tuple[Box, ...]:
        if any(x0 != x1 and y0 != y1 for x0, y0, x1, y1 in value):
            raise ValueError("линия формы — горизонтальная или вертикальная: x0 = x1 или y0 = y1")
        return value


class TitleBlockForm(RuledForm):
    labels: dict[str, Point]
    min_labels: int
    sufficient_label: str | None = None
    cells: TitleBlockCells


class ChangesTable(Section):
    label: str
    label_area: Box
    header_y: float
    column: Span
    label_column: Span
    top_tolerance: float
    header_gap: float
    row_tolerance: float
    row_right: float


class MarginColumns(Section):
    archive_label: str
    first_use_label: str
    value_reach: float
    label_slack: float
    min_overlap: Share


class TitleBlockTemplate(Section):
    width: float
    forms: dict[int, TitleBlockForm]
    continuation: RuledForm  # form 2a of the later sheets
    label_zone: tuple[Share, Share]
    label_row_tolerance: float
    scale_range: Span
    cell_padding: float
    line_row_height: float
    edge_tolerance: float
    designation_overflow: Box
    changes: ChangesTable
    margin: MarginColumns

    @property
    def label_words(self) -> set[str]:
        return {label for form in self.forms.values() for label in form.labels}


class SpecColumns(Section):
    format: Span
    zone: Span
    position: Span
    designation: Span
    name: Span
    quantity: Span
    note: Span


class SpecHeaderWords(Section):
    designation: str
    name: str


class SpecificationTemplate(Section):
    columns: SpecColumns
    header_words: SpecHeaderWords
    header_pages: int
    header_zone: Share
    header_row_tolerance: float
    scale_range: Span
    header_gap: float
    title_block_gap: float
    bottom_margin: float
    continuation_top: Share
    continuation_bottom: float
    word_range: Span
    row_tolerance: float
    designation_zone: Share
    header_height: float  # the header row under the top line of the frame
    row_height: float
    other_columns: tuple[float, ...]  # column lines of other forms: a table with one of them is not read by rows
    section_similarity: Share  # a row that is only a name this similar to a section name is its header


class TechRequirementsTemplate(Section):
    heading: str
    left: float
    right: float
    left_reach: float
    bottom_tolerance: float
    first_gap: float
    line_gap: float
    min_line_gap: float


class Forms(Section):
    sheet: Sheet
    title_block: TitleBlockTemplate
    specification: SpecificationTemplate
    tech_requirements: TechRequirementsTemplate


# ---------------------------------------------------------------- ingest.yaml


class ScanRules(Section):
    min_coverage: Share
    max_stretch: float
    check_size: int
    min_check_size: int
    max_difference: float
    max_dpi: float


class ScanQuality(Section):
    sample_width: int
    margin: Share
    min_contrast: float
    min_brightness: float
    min_sharpness: float


class RegionRules(Section):
    corner: Point  # mm: the bottom right corner of a page image where the title block is looked for
    max_skew: float  # degrees
    skew_steps: Span  # degrees: coarse and fine
    sample: float  # px per mm
    ink_density: tuple[Share, ...] = Field(min_length=1)
    max_mask_ink: Share
    line_ink: Share
    max_line_width: float  # mm
    line_band: float  # mm
    edge_band: Span  # mm inside a long line
    scale_tolerance: Share
    min_form_lines: Share
    line_on_image: Share
    line_side: float  # mm
    max_placements: int = Field(ge=0)
    wipe_ink: Share


class PageScale(Section):
    aspect_tolerance: float
    typical_dpi: tuple[float, ...]
    fallback_format: str


class TextLayout(Section):
    cell_gap: float
    line_shift: float
    vertical_angle: Span
    min_char_size: float


class MethodConfidence(Section):
    text_layer: Share
    ocr: Share
    filename: Share
    filename_name: Share
    vlm: Share


class Review(Section):
    methods: tuple[ExtractionMethod, ...]
    min_confidence: Share


class Ingest(Section):
    max_pages_per_file: int = Field(ge=0)
    render_dpi: float
    min_image_dpi: float
    scan: ScanRules
    quality: ScanQuality
    regions: RegionRules
    page_scale: PageScale
    text: TextLayout
    confidence: MethodConfidence
    review: Review


# ---------------------------------------------------------------- dictionaries.yaml


class Term(Section):
    pattern: str
    name: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value)
        return value


class Dictionaries(Section):
    spec_sections: dict[str, NodeKind]
    needs_drawing: tuple[NodeKind, ...]
    no_drawing_mark: str
    assembly_drawing_code: str
    assembly_drawing_name: str
    document_codes: tuple[str, ...]
    litera_order: tuple[str, ...]
    scales: tuple[str, ...]
    see_table: Term
    latin_lookalikes: dict[str, str]
    digit_lookalikes: dict[str, str]
    heat_treatments: tuple[Term, ...]
    geometric_tolerances: tuple[Term, ...]
    gdt_symbols: dict[str, str]
    coating_hints: tuple[str, ...]

    @field_validator("latin_lookalikes", "digit_lookalikes")
    @classmethod
    def _single_characters(cls, value: dict[str, str]) -> dict[str, str]:
        if any(len(k) != 1 or len(v) != 1 for k, v in value.items()):
            raise ValueError("ожидаются пары одиночных символов")
        return value

    @field_validator("coating_hints")
    @classmethod
    def _hints_compile(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        re.compile("|".join(value))
        return value


# ---------------------------------------------------------------- checks.yaml


class RuleSettings(Section):
    enabled: bool
    severity: Severity


class NameMatch(Section):
    methods: tuple[ExtractionMethod, ...]
    min_similarity: Share


class Checks(Section):
    rules: dict[str, RuleSettings]  # by rule id; kdcheck.checks.rules checks that every rule has its settings
    names: NameMatch


# ---------------------------------------------------------------- criticality.yaml


class TightFit(Section):
    max_it_grade: int
    score: Share
    strict_it_grade: int
    strict_score: Share


class FineRoughness(Section):
    max_ra: float
    max_rz: float
    score: Share
    kit_min_score: Share


class HighHardness(Section):
    min_hrc: float
    score: Share
    strict_min_hrc: float
    strict_score: Share


class ChemicalHeatTreatment(Section):
    treatments: tuple[str, ...]
    score: Share


class TightGeometric(Section):
    max_tolerance: float
    score: Share


class RareInKit(Section):
    min_parts: int
    score: Share


class Criticality(Section):
    tight_fit: TightFit
    fine_roughness: FineRoughness
    high_hardness: HighHardness
    chemical_heat_treatment: ChemicalHeatTreatment
    tight_geometric: TightGeometric
    rare_in_kit: RareInKit


# ---------------------------------------------------------------- models.yaml


class PdfEngine(Section):
    backend: Literal["pdfium", "pymupdf"]


class OcrCells(Section):
    """Cells of a title block and of a specification table found by their ruled lines on a page image."""

    languages: str
    page_segmentation: int
    text_height: float = Field(gt=0)  # px
    text_range: Span  # mm
    pixels_per_mm: float = Field(gt=0)
    margin: float  # mm
    min_ink: float = Field(ge=0)  # mm²
    min_word_confidence: float = Field(ge=0, le=100)
    characters: dict[str, str]  # what the cells of a field or a column may hold


class OcrEngine(Section):
    engine: Literal["tesseract"]
    enabled: bool
    languages: str
    page_segmentation: int
    min_word_confidence: float = Field(ge=0, le=100)
    cells: OcrCells


TitleBlockField = Literal["designation", "name", "material", "mass", "scale", "litera", "sheet", "sheets"]


class VlmEngine(Section):
    """A vision-language model on an OpenAI-compatible server that reads title block cells (ADR-0014)."""

    enabled: bool
    base_url: str
    model: str
    allow_remote: bool
    prompt: str
    methods: tuple[Literal["text_layer", "ocr"], ...]
    verify: bool
    fields: dict[TitleBlockField, str]
    timeout_sec: float = Field(gt=0)
    max_tokens: int = Field(gt=0)
    seed: int

    @model_validator(mode="after")
    def _stays_on_this_machine(self) -> VlmEngine:
        host = urlsplit(self.base_url).hostname
        if not self.allow_remote and host not in LOOPBACK:
            raise ValueError(
                f"base_url ведёт на другую машину ({host}): туда уйдут документы заказчика. "
                "Это допустимо только с разрешения АРМ и с allow_remote: true"
            )
        return self


class Engines(Section):
    pdf: PdfEngine
    ocr: OcrEngine
    vlm: VlmEngine


# ---------------------------------------------------------------- all files


class Settings(Section):
    forms: Forms
    ingest: Ingest
    dictionaries: Dictionaries
    checks: Checks
    criticality: Criticality
    models: Engines

    @model_validator(mode="after")
    def _files_agree(self) -> Settings:
        if self.ingest.page_scale.fallback_format not in self.forms.sheet.formats:
            raise ValueError("ingest.yaml: page_scale.fallback_format нет среди sheet.formats в forms.yaml")
        known = {t.name for t in self.dictionaries.heat_treatments}
        unknown = [t for t in self.criticality.chemical_heat_treatment.treatments if t not in known]
        if unknown:
            raise ValueError(
                f"criticality.yaml: chemical_heat_treatment.treatments {unknown} нет среди heat_treatments "
                "в dictionaries.yaml"
            )
        return self


FILES = {name: f"{name}.yaml" for name in Settings.model_fields}


def config_dir(path: Path | None = None) -> Path:
    """``path``, else ``ARM_CONFIG_DIR``, else ``configs/`` of the checkout this package runs from."""
    if path is not None:
        return path
    if env := os.environ.get(CONFIG_DIR_ENV):
        return Path(env)
    # packages/kdcheck/src/kdcheck/config.py → the repository root
    return Path(__file__).resolve().parents[4] / "configs"


def prompts_dir(path: Path | None = None) -> Path:
    """``path``, else ``ARM_PROMPTS_DIR``, else ``prompts/`` of the checkout this package runs from."""
    if path is not None:
        return path
    if env := os.environ.get(PROMPTS_DIR_ENV):
        return Path(env)
    return Path(__file__).resolve().parents[4] / "prompts"


def load_settings(
    path: Path | None = None,
    *,
    use_ocr: bool | None = None,
    max_pages: int | None = None,
    pdf_backend: str | None = None,
    use_vlm: bool | None = None,
) -> Settings:
    """Settings from :func:`config_dir` with the overrides in effect: the arguments, else the environment variables."""
    directory = config_dir(path)
    raw: dict[str, Any] = {}
    for section, name in FILES.items():
        file = directory / name
        try:
            raw[section] = yaml.safe_load(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(
                f"Нет файла настроек {file}: укажите каталог настроек в --config или {CONFIG_DIR_ENV}"
            ) from None
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ConfigError(f"Файл настроек {file} не читается: {exc}") from None
    if use_ocr is None and "ARM_OCR" in os.environ:
        use_ocr = os.environ["ARM_OCR"] != "0"
    if use_vlm is None and "ARM_VLM" in os.environ:
        use_vlm = os.environ["ARM_VLM"] != "0"
    if pdf_backend is None:
        pdf_backend = os.environ.get("ARM_PDF_BACKEND") or None
    if max_pages is None and (env_pages := os.environ.get("ARM_MAX_PAGES")):
        try:
            max_pages = int(env_pages)
        except ValueError:
            raise ConfigError(f"ARM_MAX_PAGES: ожидается целое число, получено {env_pages!r}") from None
    _override(raw, ("models", "ocr", "enabled"), use_ocr)
    _override(raw, ("models", "pdf", "backend"), pdf_backend)
    _override(raw, ("ingest", "max_pages_per_file"), max_pages)
    _override(raw, ("models", "vlm", "enabled"), use_vlm)
    _override(raw, ("models", "vlm", "base_url"), os.environ.get("ARM_VLM_URL") or None)
    _override(raw, ("models", "vlm", "model"), os.environ.get("ARM_VLM_MODEL") or None)
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_describe(exc, directory)) from None


def _override(raw: dict[str, Any], keys: tuple[str, ...], value: object) -> None:
    """Put ``value`` at ``keys`` unless it is None; a malformed file is left for the validation to report."""
    node: Any = raw
    for key in keys[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
    if value is not None and isinstance(node, dict):
        node[keys[-1]] = value


def _describe(exc: ValidationError, directory: Path) -> str:
    problems = []
    for error in exc.errors():
        section, *field = [str(part) for part in error["loc"]] or ["?"]
        where = f"{directory / FILES[section]}" if section in FILES else str(directory)
        problems.append(f"{where}: {'.'.join(field) or '—'}: {error['msg']}")
    return "Настройки не прошли проверку:\n" + "\n".join(problems)


def settings_sha256(settings: Settings) -> str:
    """SHA-256 of the settings in effect: values and their order count, comments and layout of the files do not.

    Order counts because some lists and mappings are ordered rules, such as the first matching heat treatment.
    """
    data = json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
