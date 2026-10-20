// Generated from docs/report.schema.json by scripts/report-types.mjs. Do not edit: run `npm run gen:types`.

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
  documents_by_status: {
    [k: string]: number;
  };
  documents_by_kind: {
    [k: string]: number;
  };
  specifications: number;
  drawings: number;
  tree_items: number;
  parts: number;
  parameters: number;
  discrepancies_total: number;
  discrepancies_by_type: {
    [k: string]: number;
  };
  critical_parameters: number;
  /**
   * Доля позиций, требующих чертежа, для которых он найден
   */
  completeness_ratio?: number | null;
}
export interface DocumentInfo {
  id: string;
  file_name: string;
  /**
   * Путь относительно корня комплекта
   */
  path: string;
  size_bytes: number;
  media_type?: string | null;
  sha256?: string | null;
  pages: PageInfo[];
  kind: "specification" | "assembly_drawing" | "part_drawing" | "drawing" | "other" | "unknown";
  origin: "vector" | "scan" | "mixed" | "unknown";
  status: "ok" | "warning" | "damaged" | "unreadable" | "foreign" | "unsupported";
  /**
   * Пояснение для проблемных файлов
   */
  problem?: string | null;
  designation?: string | null;
  name?: string | null;
  notes: string[];
}
export interface PageInfo {
  number: number;
  width: number;
  height: number;
  unit: "pt" | "px";
  has_text: boolean;
}
export interface Specification {
  document_id: string;
  designation?: Extracted | null;
  name?: Extracted | null;
  litera?: Extracted | null;
  revision?: Extracted | null;
  rows: SpecRow[];
}
/**
 * Извлечённое значение с прослеживаемостью.
 */
export interface Extracted {
  value?: string | number | null;
  raw?: string | null;
  confidence?: number | null;
  source?: SourceRef | null;
}
/**
 * Откуда получено значение: документ, страница и область страницы.
 */
export interface SourceRef {
  document_id: string;
  /**
   * Номер страницы, начиная с 1
   */
  page: number;
  /**
   * Область [x0, y0, x1, y1] в долях ширины/высоты страницы, начало — левый верхний угол
   */
  bbox?: [number, number, number, number] | null;
  /**
   * Исходный распознанный текст
   */
  text?: string | null;
  method?: ("text_layer" | "ocr" | "vlm" | "llm" | "filename" | "rule" | "manual") | null;
  confidence?: number | null;
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
export interface TreeNode {
  id: string;
  designation?: string | null;
  name?: string | null;
  kind: "kit" | "assembly" | "part" | "standard" | "other_item" | "material" | "document" | "unknown";
  section?: string | null;
  position?: string | null;
  quantity?: number | null;
  /**
   * Количество на изделие верхнего уровня
   */
  total_quantity?: number | null;
  spec_document_id?: string | null;
  drawing_document_ids: string[];
  status: "ok" | "missing" | "warning" | "not_required";
  discrepancy_ids: string[];
  children: TreeNode[];
}
export interface Discrepancy {
  id: string;
  type:
    | "missing_drawing"
    | "orphan_drawing"
    | "name_mismatch"
    | "duplicate_designation"
    | "revision_mismatch"
    | "missing_specification";
  severity: "high" | "medium" | "low";
  designation?: string | null;
  title: string;
  message: string;
  expected?: string | null;
  actual?: string | null;
  document_ids: string[];
  tree_node_ids: string[];
  sources: SourceRef[];
}
/**
 * Извлечённые данные одного чертежа.
 */
export interface PartRecord {
  document_id: string;
  designation?: string | null;
  name?: string | null;
  kind: "specification" | "assembly_drawing" | "part_drawing" | "drawing" | "other" | "unknown";
  title_block: TitleBlock;
  /**
   * Пункты технических требований
   */
  requirements: Extracted[];
  parameters: Parameter[];
}
/**
 * Поля основной надписи (ГОСТ 2.104).
 */
export interface TitleBlock {
  designation?: Extracted | null;
  name?: Extracted | null;
  material?: Extracted | null;
  mass?: Extracted | null;
  scale?: Extracted | null;
  litera?: Extracted | null;
  /**
   * Номер последнего изменения
   */
  revision?: Extracted | null;
  archive_number?: Extracted | null;
  /**
   * Первое применение
   */
  first_use?: Extracted | null;
  sheet?: Extracted | null;
  sheets?: Extracted | null;
  format?: Extracted | null;
}
/**
 * Параметр технических требований в единой структуре.
 */
export interface Parameter {
  id: string;
  category:
    | "dimension_tolerance"
    | "general_tolerance"
    | "roughness"
    | "geometric"
    | "heat_treatment"
    | "hardness"
    | "coating"
    | "other";
  /**
   * Короткое имя: Ra, Ø50k6, Твёрдость, Покрытие…
   */
  name: string;
  /**
   * Исходный текст требования
   */
  text: string;
  value?: number | null;
  value_min?: number | null;
  value_max?: number | null;
  unit?: string | null;
  /**
   * Поле допуска (H7), шкала (HRC), базы, вид ТО…
   */
  qualifier?: string | null;
  it_grade?: number | null;
  /**
   * Общее требование (неуказанные отклонения, шероховатость остального)
   */
  general: boolean;
  critical: boolean;
  source?: SourceRef | null;
  confidence?: number | null;
}
export interface CriticalParameter {
  id: string;
  document_id: string;
  designation?: string | null;
  part_name?: string | null;
  parameter_id?: string | null;
  category:
    | "dimension_tolerance"
    | "general_tolerance"
    | "roughness"
    | "geometric"
    | "heat_treatment"
    | "hardness"
    | "coating"
    | "other";
  text: string;
  /**
   * Код правила отбора
   */
  rule: string;
  /**
   * Обоснование для технолога
   */
  reason: string;
  score: number;
  source?: SourceRef | null;
}
export interface QualityReport {
  dataset: string;
  methodology: string;
  metrics: QualityMetric[];
  errors: ErrorCase[];
  limitations: string[];
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
export interface LogEntry {
  level: "info" | "warning" | "error";
  message: string;
  document_id?: string | null;
}
