// Report types are generated from the JSON Schema (docs/report.schema.json): src/api/report.generated.ts.
// API types below mirror backend/arm_web/models.py (Job) and the /api/health response.
import type {
  Discrepancy,
  DocumentInfo,
  Parameter,
  SourceRef,
  Summary,
  TitleBlock,
  TreeNode,
} from "./report.generated";

export type * from "./report.generated";

export type ExtractionMethod = NonNullable<SourceRef["method"]>;
export type DocumentKind = DocumentInfo["kind"];
export type DocumentStatus = DocumentInfo["status"];
export type DocumentOrigin = DocumentInfo["origin"];
export type NodeKind = TreeNode["kind"];
export type NodeStatus = TreeNode["status"];
export type DiscrepancyType = Discrepancy["type"];
export type Severity = Discrepancy["severity"];
export type ParameterCategory = Parameter["category"];

/** Page region [x0, y0, x1, y1] as fractions of page width and height, origin at the top left. */
export type BBox = NonNullable<SourceRef["bbox"]>;

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
] as const satisfies readonly (keyof TitleBlock)[];
export type TitleField = (typeof TITLE_FIELDS)[number];

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
