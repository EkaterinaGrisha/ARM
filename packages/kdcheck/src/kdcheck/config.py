"""Settings of the core: GOST form templates, thresholds, dictionaries and engines from ``configs/*.yaml`` (ADR-0012).

Each file is one section of :class:`Settings`. The files are checked against these models, so an unknown or missing
key or a wrong value stops the run with the file and the field named. The directory comes from ``kdcheck run
--config``, else from ``ARM_CONFIG_DIR``, else it is ``configs/`` of the repository checkout. ``ARM_OCR``,
``ARM_PDF_BACKEND``, ``ARM_MAX_PAGES``, ``ARM_VLM``, ``ARM_VLM_URL`` and ``ARM_VLM_MODEL`` override single values.
The hash of the settings in effect goes into the report, so a result can be traced to the settings that gave it.
Prompts of the models are versioned files in ``prompts/`` of the checkout, or in ``ARM_PROMPTS_DIR``. Model files that
are not packages, such as the PP-OCRv5 recognizer, are in ``models/`` of the checkout, or in ``ARM_MODELS_DIR``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import string
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kdcheck.schemas import DocumentKind, ExtractionMethod, NodeKind, Severity

CONFIG_DIR_ENV = "ARM_CONFIG_DIR"
PROMPTS_DIR_ENV = "ARM_PROMPTS_DIR"
MODELS_DIR_ENV = "ARM_MODELS_DIR"
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


class ContinuationCells(Section):
    designation: Box
    sheet: Box


class ContinuationForm(RuledForm):
    """Form 2a of the later sheets: the designation of the document and the number of the sheet."""

    cells: ContinuationCells
    sheet_label: str  # over the sheet cell: on a page with a text layer it tells form 2a from other text


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
    continuation: ContinuationForm  # form 2a of the later sheets
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


class GroupColumns(Section):
    format: Span
    zone: Span
    position: Span
    designation: Span
    name: Span
    note: Span


class VariantColumns(Section):
    left: float  # mm from the left line of the table: the first column of quantities, the base variant's
    width: float = Field(gt=0)
    count: int = Field(ge=1)


class GroupForm(Section):
    """A form of a group specification of GOST 2.113 by variant B: a column of quantities for each variant."""

    width: float = Field(gt=0)  # mm: the table ends at the right edge of the title block
    columns: GroupColumns
    variants: VariantColumns


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
    frame_reach: float = Field(ge=0)  # mm: the outer column lines of a table on a page image, from the block's edges
    groups: dict[str, GroupForm]  # group specifications of GOST 2.113 by variant B (ADR-0025)
    subhead_height: float = Field(gt=0)  # mm: the numbers of the variants above the header line of a group one
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
    row_indent: float  # without the item numbers, a row starting this far right of the rows below is the field's
    row_overlap: float = Field(gt=0, le=1)  # pieces of one row share this part of the height of the lower one


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


class BlankSheet(Section):
    scale: float = Field(gt=0)  # px per mm of the copy measured
    margin: float = Field(ge=0)  # mm left out at each edge
    min_paper: float  # grey level: a darker sheet is no blank paper but a dark scan
    detail: int = Field(ge=3)  # px of that copy: dark detail narrower than this against the paper around it is a mark
    min_darkness: float  # grey levels below that paper for a pixel to be a mark
    cell: float = Field(gt=0)  # mm: the side of a cell of the sheet
    cell_marks: Share  # share of a cell in marks for the cell to hold marks
    max_cells: int = Field(ge=0)  # cells with marks a blank sheet may have
    min_cells: int = Field(ge=1)  # cells of a sheet at the least; a smaller page is measured whole


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
    corner_slack: Point  # mm, right and below: how far the corner of a title block may lie from that of the image
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


class FileNames(Section):
    sheet_suffix: str
    unknown_shape_share: Share

    @field_validator("sheet_suffix")
    @classmethod
    def _compiles(cls, value: str) -> str:
        if re.compile(value).groups != 1:
            raise ValueError("в выражении суффикса листа одна группа — номер листа")
        return value

    def sheet(self, stem: str) -> int | None:
        """The number of the sheet a file name gives: «Рама в сборе(2)» → 2."""
        m = re.search(self.sheet_suffix, stem)
        return int(m.group(1)) if m else None


class Ingest(Section):
    max_pages_per_file: int = Field(ge=0)
    page_timeout_sec: float = Field(ge=0)
    max_image_pixels: int = Field(ge=0)  # 0: no limit
    render_dpi: float
    min_image_dpi: float
    scan: ScanRules
    quality: ScanQuality
    blank: BlankSheet
    regions: RegionRules
    page_scale: PageScale
    text: TextLayout
    confidence: MethodConfidence
    review: Review
    file_names: FileNames


# ---------------------------------------------------------------- dictionaries.yaml


class Term(Section):
    pattern: str
    name: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value)
        return value


DIGIT = "{d}"  # in a designation grammar: a digit, or a letter recognized instead of one
CORE_GROUPS = ("exec", "code")  # groups the core adds after every grammar: the execution and the document code


class Grammar(Section):
    """A system of designation (ADR-0016): its parts, the way its value is written, examples of both kinds."""

    name: str
    pattern: str
    canonical: str
    numbers: tuple[str, ...] = ()
    upper: bool
    always: bool = False  # on in every kit; any other grammar only in a kit whose file names show it
    examples: dict[str, str] = Field(min_length=1)
    counterexamples: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _groups_agree(self) -> Grammar:
        groups = set(re.compile(self.pattern.replace(DIGIT, r"\d")).groupindex)
        if taken := groups & set(CORE_GROUPS):
            raise ValueError(f"группы {sorted(taken)} добавляет ядро после каждой грамматики")
        wanted = {field for _, field, _, _ in string.Formatter().parse(self.canonical) if field}
        if missing := (wanted | set(self.numbers)) - groups:
            raise ValueError(f"в pattern нет групп {sorted(missing)}")
        if any(c.isspace() for c in self.canonical):
            raise ValueError("canonical без пробелов: после пробела идёт код документа")
        return self


class RowDesignations(Section):
    """Designations of specification rows checked against the designations of the kit (ADR-0020)."""

    methods: tuple[Literal["text_layer", "ocr", "vlm"], ...]  # rows read this way are checked
    min_digits: int = Field(ge=1)  # a reading with fewer digits names nothing
    one_digit: bool  # the digits may differ by one digit dropped or doubled, never by one read as another


class FileDesignations(Section):
    """The designation of a title block read from a raster decided against the designation of its file name
    (ADR-0022)."""

    methods: tuple[Literal["text_layer", "ocr", "vlm"], ...]  # a title block read this way may take the file name's
    min_digits: int = Field(ge=1)  # a file name with fewer digits in its designation decides nothing
    one_digit: bool  # the digits may differ by one digit dropped or doubled, never by one read as another


class Dictionaries(Section):
    spec_sections: dict[str, NodeKind]
    needs_drawing: tuple[NodeKind, ...]
    no_drawing_mark: str
    no_drawing_misreads: dict[str, str]
    variable_data: str
    assembly_drawing_code: str
    assembly_drawing_name: str
    document_codes: tuple[str, ...]
    filename_codes: dict[str, DocumentKind]
    designations: tuple[Grammar, ...] = Field(min_length=1)
    litera_order: tuple[str, ...]
    scales: tuple[str, ...]
    see_table: Term
    latin_lookalikes: dict[str, str]
    digit_lookalikes: dict[str, str]
    heat_treatments: tuple[Term, ...]
    geometric_tolerances: tuple[Term, ...]
    gdt_symbols: dict[str, str]
    coating_hints: tuple[str, ...]
    row_designations: RowDesignations
    file_designations: FileDesignations

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


class SheetCount(Section):
    count_methods: tuple[ExtractionMethod, ...]


class Checks(Section):
    rules: dict[str, RuleSettings]  # by rule id; kdcheck.checks.rules checks that every rule has its settings
    names: NameMatch
    sheets: SheetCount


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

    engine: Literal["ppocr", "tesseract"]  # ppocr falls back to Tesseract without its model files (ADR-0024)
    languages: str
    page_segmentation: int
    text_height: float = Field(gt=0)  # px
    text_range: Span  # mm
    pixels_per_mm: float = Field(gt=0)
    margin: float  # mm
    min_ink: float = Field(ge=0)  # mm²
    min_word_confidence: float = Field(ge=0, le=100)
    characters: dict[str, str]  # what the cells of a field or a column may hold


class OcrPpocr(Section):
    """The PP-OCRv5 text line recognizer that reads cells on the CPU (ADR-0024), from the files that
    ``scripts/fetch_ppocr.sh`` puts in ``models/ppocr``."""

    model: str  # the model of PaddleOCR: models/ppocr/<model>.onnx and <model>.json
    repository: str  # on Hugging Face
    revision: str
    sha256: dict[str, str]  # of the files of that revision the ONNX file is made from
    height: int = Field(gt=0)  # px: the recognizer reads a line this high
    valley: float = Field(ge=0, le=1)  # of the median row with ink: rows with less part two lines
    speck_gap: float = Field(ge=0)  # line heights: ink this far from the rest of a line and lighter than min_ink
    margin: float = Field(ge=0)  # of the line height: paper kept around a line cut close to its ink


class OcrEngine(Section):
    engine: Literal["tesseract"]
    enabled: bool
    languages: str
    page_segmentation: int
    min_word_confidence: float = Field(ge=0, le=100)
    cells: OcrCells
    ppocr: OcrPpocr


TitleBlockField = Literal["designation", "name", "material", "mass", "scale", "litera", "sheet", "sheets"]


class VlmRows(Section):
    """Cells of specification rows of rasters and scans the model reads (ADR-0021)."""

    prompt: str
    fields: dict[Literal["designation", "name"], str]  # the columns of GOST 2.106, as the prompt names them


class VlmRequirements(Section):
    """The technical requirements block of rasters and scans the model reads (ADR-0023)."""

    prompt: str
    max_tokens: int = Field(gt=0)  # a block of several items is longer than a cell
    target_dpi: int = Field(gt=0)  # a coarser image is enlarged to this before the model reads it
    pad_mm: float = Field(ge=0)  # around the lines OCR found
    line_match: float = Field(ge=0, le=1)  # a line of the answer this alike to a row of OCR takes its place


class VlmEngine(Section):
    """A vision-language model on an OpenAI-compatible server that reads title block cells (ADR-0014)."""

    enabled: bool
    base_url: str
    model: str
    allow_remote: bool
    prompt: str
    methods: tuple[Literal["text_layer", "ocr"], ...]
    verify: bool
    conflict: Literal["ocr", "model"]  # whose reading a cell keeps when the two differ (ADR-0019)
    fields: dict[TitleBlockField, str]
    rows: VlmRows
    requirements: VlmRequirements
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


def models_dir(path: Path | None = None) -> Path:
    """``path``, else ``ARM_MODELS_DIR``, else ``models/`` of the checkout this package runs from."""
    if path is not None:
        return path
    if env := os.environ.get(MODELS_DIR_ENV):
        return Path(env)
    return Path(__file__).resolve().parents[4] / "models"


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
