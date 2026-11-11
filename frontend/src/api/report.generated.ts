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
  /**
   * Данные внешних конвейеров: ключ — имя конвейера или команды. С версии 1.2
   */
  extensions: {
    [k: string]: unknown;
  };
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
  /**
   * Движки и библиотеки с версиями. С версии 1.1
   */
  engines: EngineInfo[];
  /**
   * SHA-256 настроек configs/*.yaml, с которыми получен отчёт. С версии 1.1
   */
  config_sha256?: string | null;
}
/**
 * Движок или библиотека, от которых зависит результат, и их версия. С версии 1.1.
 */
export interface EngineInfo {
  name: string;
  version?: string | null;
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
  status:
    "ok" | "warning" | "damaged" | "unreadable" | "blank" | "foreign" | "unsupported" | "encrypted" | "duplicate_file";
  /**
   * Пояснение для проблемных файлов
   */
  problem?: string | null;
  designation?: string | null;
  name?: string | null;
  /**
   * Номер листа документа, с которого начинается файл: по основной надписи, иначе по номеру в скобках в конце имени файла. С версии 1.4
   */
  sheet?: number | null;
  /**
   * Файл — последующий лист документа, начатого в файле с этим id; спецификация и запись детали — по тому файлу. Пусто у первого листа и у документа в одном файле. С версии 1.4
   */
  sheet_of?: string | null;
  notes: string[];
}
/**
 * Страница файла. Ширина и высота 0 — страница не открылась, размер неизвестен.
 */
export interface PageInfo {
  number: number;
  width: number;
  height: number;
  unit: "pt" | "px";
  has_text: boolean;
  /**
   * Формат листа по ГОСТ 2.301 по размеру страницы: A4, A3, A4x3…; нет — размер не подходит ни к одному формату или неизвестен. С версии 1.3
   */
  format?: string | null;
  /**
   * Почему страница не прочитана или не разобрана; остальные страницы обработаны. С версии 1.3
   */
  problem?: string | null;
}
export interface Specification {
  document_id: string;
  designation?: Extracted | null;
  name?: Extracted | null;
  litera?: Extracted | null;
  revision?: Extracted | null;
  /**
   * «Листов» основной надписи первого листа. С версии 1.4
   */
  sheets?: Extracted | null;
  /**
   * «Перв. примен.» первого листа: обозначение, где изделие записано впервые. С версии 1.8
   */
  first_use?: Extracted | null;
  /**
   * Номера исполнений групповой спецификации ГОСТ 2.113: по варианту Б — в порядке граф, по варианту А — в порядке заголовков переменных данных; «–» — базовое, затем «01», «02»…; обозначение исполнения — обозначение спецификации с «-01». Пусто у единичной. С версии 1.6, вариант А — с версии 1.7
   */
  variants: string[];
  rows: SpecRow[];
}
/**
 * Извлечённое значение с прослеживаемостью.
 */
export interface Extracted {
  value?: string | number | null;
  raw?: string | null;
  confidence?: number | null;
  /**
   * ok — значение принято; low_confidence — проверить по документу; missing — значение не найдено; conflict — источники дают разные значения. С версии 1.2
   */
  status?: ("ok" | "low_confidence" | "missing" | "conflict") | null;
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
  /**
   * Движок, прочитавший текст: pdfium, tesseract… С версии 1.1
   */
  engine?: string | null;
  /**
   * Версия движка. С версии 1.1
   */
  engine_version?: string | null;
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
  /**
   * Количество по исполнениям групповой спецификации по варианту Б ГОСТ 2.113, вместо `quantity`: номер исполнения из подзаголовка графы «Кол. на исполн.» («–» — базовое, «01», «02»…) → количество, у документа «×». Исполнения без количества в строке нет. С версии 1.6
   */
  quantities: {
    [k: string]: Extracted;
  };
  /**
   * Исполнение, к переменным данным которого относится строка групповой спецификации по варианту А ГОСТ 2.113: номер из заголовка с обозначением исполнения над строкой («–» — базовое, «01», «02»…). Пусто у постоянных данных и у единичной спецификации. С версии 1.7
   */
  variant?: string | null;
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
  /**
   * Правило, которое нашло расхождение: B1.NAME.001… С версии 1.2
   */
  rule_id?: string | null;
  type:
    | "missing_drawing"
    | "orphan_drawing"
    | "name_mismatch"
    | "duplicate_designation"
    | "revision_mismatch"
    | "missing_specification"
    | "sheets_missing"
    | "format_mismatch"
    | "section_mismatch"
    | "position_anomaly"
    | "first_use_mismatch";
  severity: "high" | "medium" | "low";
  designation?: string | null;
  title: string;
  message: string;
  expected?: string | null;
  actual?: string | null;
  document_ids: string[];
  tree_node_ids: string[];
  sources: SourceRef[];
  /**
   * Предварительное: в комплекте есть документы без прочитанного обозначения, и искомый может быть среди них; они перечислены в сообщении и в `document_ids`. С версии 1.8
   */
  preliminary: boolean;
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
   * Нижнее предельное отклонение размера, мм: из поля допуска по ГОСТ 25346 (Ø40H7 → 0) или из записи «±»; `value_min` — наименьший предельный размер. С версии 1.9
   */
  lower_deviation?: number | null;
  /**
   * Верхнее предельное отклонение размера, мм (Ø40H7 → +0,025); `value_max` — наибольший предельный размер. С версии 1.9
   */
  upper_deviation?: number | null;
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
