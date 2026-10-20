// Формат отчёта — зеркало backend/arm_web/models.py (JSON Schema: docs/report.schema.json).

export type ExtractionMethod = "text_layer" | "ocr" | "vlm" | "llm" | "filename" | "rule" | "manual";
export type DocumentKind = "specification" | "assembly_drawing" | "part_drawing" | "drawing" | "other" | "unknown";
export type DocumentStatus = "ok" | "warning" | "damaged" | "unreadable" | "foreign" | "unsupported";
export type DocumentOrigin = "vector" | "scan" | "mixed" | "unknown";
export type NodeKind = "kit" | "assembly" | "part" | "standard" | "other_item" | "material" | "document" | "unknown";
export type NodeStatus = "ok" | "missing" | "warning" | "not_required";
export type DiscrepancyType =
  | "missing_drawing"
  | "orphan_drawing"
  | "name_mismatch"
  | "duplicate_designation"
  | "revision_mismatch"
  | "missing_specification";
export type Severity = "high" | "medium" | "low";
export type ParameterCategory =
  | "dimension_tolerance"
  | "general_tolerance"
  | "roughness"
  | "geometric"
  | "heat_treatment"
  | "hardness"
  | "coating"
  | "other";

/** Область страницы [x0, y0, x1, y1] в долях ширины/высоты, начало — левый верхний угол. */
export type BBox = [number, number, number, number];

export interface SourceRef {
  document_id: string;
  page: number;
  bbox?: BBox | null;
  text?: string | null;
  method?: ExtractionMethod | null;
  confidence?: number | null;
}

export interface Extracted {
  value?: string | number | null;
  raw?: string | null;
  confidence?: number | null;
  source?: SourceRef | null;
}

export interface PageInfo {
  number: number;
  width: number;
  height: number;
  unit: "pt" | "px";
  has_text: boolean;
}

export interface DocumentInfo {
  id: string;
  file_name: string;
  path: string;
  size_bytes: number;
  media_type?: string | null;
  sha256?: string | null;
  pages: PageInfo[];
  kind: DocumentKind;
  origin: DocumentOrigin;
  status: DocumentStatus;
  problem?: string | null;
  designation?: string | null;
  name?: string | null;
  notes: string[];
}

export const TITLE_FIELDS = [
  "designation",
  "name",
  "material",
  "mass",
  "scale",
  "litera",
  "revision",
  "archive_number",
  "first_use",
  "sheet",
  "sheets",
  "format",
] as const;
export type TitleField = (typeof TITLE_FIELDS)[number];
export type TitleBlock = Partial<Record<TitleField, Extracted | null>>;

export interface Parameter {
  id: string;
  category: ParameterCategory;
  name: string;
  text: string;
  value?: number | null;
  value_min?: number | null;
  value_max?: number | null;
  unit?: string | null;
  qualifier?: string | null;
  it_grade?: number | null;
  general: boolean;
  critical: boolean;
  source?: SourceRef | null;
  confidence?: number | null;
}

export interface PartRecord {
  document_id: string;
  designation?: string | null;
  name?: string | null;
  kind: DocumentKind;
  title_block: TitleBlock;
  requirements: Extracted[];
  parameters: Parameter[];
}

export interface SpecRow {
  id: string;
  section?: string | null;
  format?: Extracted | null;
  zone?: Extracted | null;
  position?: Extracted | null;
  designation?: Extracted | null;
  name?: Extracted | null;
  quantity?: Extracted | null;
  note?: Extracted | null;
  source?: SourceRef | null;
}

export interface Specification {
  document_id: string;
  designation?: Extracted | null;
  name?: Extracted | null;
  litera?: Extracted | null;
  revision?: Extracted | null;
  rows: SpecRow[];
}

export interface TreeNode {
  id: string;
  designation?: string | null;
  name?: string | null;
  kind: NodeKind;
  section?: string | null;
  position?: string | null;
  quantity?: number | null;
  total_quantity?: number | null;
  spec_document_id?: string | null;
  drawing_document_ids: string[];
  status: NodeStatus;
  discrepancy_ids: string[];
  children: TreeNode[];
}

export interface Discrepancy {
  id: string;
  type: DiscrepancyType;
  severity: Severity;
  designation?: string | null;
  title: string;
  message: string;
  expected?: string | null;
  actual?: string | null;
  document_ids: string[];
  tree_node_ids: string[];
  sources: SourceRef[];
}

export interface CriticalParameter {
  id: string;
  document_id: string;
  designation?: string | null;
  part_name?: string | null;
  parameter_id?: string | null;
  category: ParameterCategory;
  text: string;
  rule: string;
  reason: string;
  score: number;
  source?: SourceRef | null;
}

export interface QualityMetric {
  block: "completeness" | "extraction";
  name: string;
  precision?: number | null;
  recall?: number | null;
  f1?: number | null;
  accuracy?: number | null;
  support?: number | null;
}

export interface ErrorCase {
  block: "completeness" | "extraction";
  kind: "false_positive" | "false_negative" | "wrong_value";
  title: string;
  description: string;
  document_id?: string | null;
}

export interface QualityReport {
  dataset: string;
  methodology: string;
  metrics: QualityMetric[];
  errors: ErrorCase[];
  limitations: string[];
}

export interface LogEntry {
  level: "info" | "warning" | "error";
  message: string;
  document_id?: string | null;
}

export interface KitInfo {
  id: string;
  name: string;
  root_designation?: string | null;
  root_name?: string | null;
  created_at?: string | null;
  processed_at?: string | null;
  duration_sec?: number | null;
  pipeline_name?: string | null;
  pipeline_version?: string | null;
}

export interface Summary {
  documents_total: number;
  documents_by_status: Partial<Record<DocumentStatus, number>>;
  documents_by_kind: Partial<Record<DocumentKind, number>>;
  specifications: number;
  drawings: number;
  tree_items: number;
  parts: number;
  parameters: number;
  discrepancies_total: number;
  discrepancies_by_type: Partial<Record<DiscrepancyType, number>>;
  critical_parameters: number;
  completeness_ratio?: number | null;
}

export interface Report {
  schema_version: string;
  kit: KitInfo;
  summary: Summary;
  documents: DocumentInfo[];
  specifications: Specification[];
  tree: TreeNode[];
  discrepancies: Discrepancy[];
  parts: PartRecord[];
  critical_parameters: CriticalParameter[];
  quality?: QualityReport | null;
  log: LogEntry[];
}

export type JobStatus = "queued" | "running" | "done" | "failed";

export interface Job {
  id: string;
  name: string;
  status: JobStatus;
  stage?: string | null;
  progress: number;
  message?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  files_count: number;
  total_bytes: number;
  error?: string | null;
  summary?: Summary | null;
  root_designation?: string | null;
}

export interface Health {
  status: string;
  version: string;
  pipeline: string;
  pipeline_version: string;
  ocr: boolean;
}
