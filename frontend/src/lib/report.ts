import type {
  BBox,
  Discrepancy,
  DocumentInfo,
  Extracted,
  PartRecord,
  Report,
  SourceRef,
  Specification,
  TitleField,
  TreeNode,
} from "../api/types";
import { extractedText } from "./format";
import { DISCREPANCY_TYPES, PARAMETER_CATEGORY, TITLE_FIELD_LABELS } from "./labels";

/** Значение отчёта, привязанное к области страницы, — для просмотрщика документов. */
export interface SourceEntry {
  key: string;
  group: string;
  label: string;
  value: string;
  source: SourceRef;
}

export interface ReportIndex {
  docs: Map<string, DocumentInfo>;
  parts: Map<string, PartRecord>;
  specs: Map<string, Specification>;
  discrepancies: Map<string, Discrepancy>;
  nodes: Map<string, TreeNode>;
  sourcesByDoc: Map<string, SourceEntry[]>;
  docsByDesignation: Map<string, DocumentInfo[]>;
}

export function sameBox(a: BBox | null | undefined, b: BBox | null | undefined, eps = 0.002): boolean {
  if (!a || !b) return false;
  return a.every((v, i) => Math.abs(v - b[i]) <= eps);
}

export function buildIndex(report: Report): ReportIndex {
  const sourcesByDoc = new Map<string, SourceEntry[]>();
  let n = 0;
  const push = (group: string, label: string, value: string, source: SourceRef | null | undefined) => {
    if (!source || !source.document_id) return;
    const list = sourcesByDoc.get(source.document_id) ?? [];
    list.push({ key: `s${n++}`, group, label, value, source });
    sourcesByDoc.set(source.document_id, list);
  };
  const pushExtracted = (group: string, label: string, ex: Extracted | null | undefined) => {
    if (ex?.source) push(group, label, extractedText(ex) || ex.raw || "", ex.source);
  };

  for (const part of report.parts) {
    for (const [field, ex] of Object.entries(part.title_block) as [TitleField, Extracted | null][]) {
      pushExtracted("Основная надпись", TITLE_FIELD_LABELS[field] ?? field, ex);
    }
    part.requirements.forEach((req, i) => pushExtracted("Технические требования", `п. ${i + 1}`, req));
    for (const p of part.parameters) {
      push("Параметры", PARAMETER_CATEGORY[p.category] ?? p.category, p.name, p.source);
    }
  }
  for (const spec of report.specifications) {
    pushExtracted("Основная надпись", "Обозначение", spec.designation);
    pushExtracted("Основная надпись", "Наименование", spec.name);
    pushExtracted("Основная надпись", "Литера", spec.litera);
    pushExtracted("Основная надпись", "Изм.", spec.revision);
    for (const row of spec.rows) {
      const label = row.position?.value ? `Поз. ${row.position.value}` : (row.section ?? "Строка");
      const value = [extractedText(row.designation), extractedText(row.name), extractedText(row.quantity) && `× ${extractedText(row.quantity)}`]
        .filter(Boolean)
        .join(" ");
      push("Спецификация", label, value, row.source);
    }
  }
  for (const d of report.discrepancies) {
    for (const s of d.sources) push("Расхождения", DISCREPANCY_TYPES[d.type]?.label ?? d.type, d.designation ?? d.title, s);
  }

  const nodes = new Map<string, TreeNode>();
  walkTree(report.tree, (node) => nodes.set(node.id, node));

  const docsByDesignation = new Map<string, DocumentInfo[]>();
  for (const doc of report.documents) {
    if (!doc.designation) continue;
    const list = docsByDesignation.get(doc.designation) ?? [];
    list.push(doc);
    docsByDesignation.set(doc.designation, list);
  }

  return {
    docs: new Map(report.documents.map((d) => [d.id, d])),
    parts: new Map(report.parts.map((p) => [p.document_id, p])),
    specs: new Map(report.specifications.map((s) => [s.document_id, s])),
    discrepancies: new Map(report.discrepancies.map((d) => [d.id, d])),
    nodes,
    sourcesByDoc,
    docsByDesignation,
  };
}

export function walkTree(nodes: TreeNode[], fn: (node: TreeNode, depth: number, parent: TreeNode | null) => void, depth = 0, parent: TreeNode | null = null): void {
  for (const node of nodes) {
    fn(node, depth, parent);
    walkTree(node.children, fn, depth + 1, node);
  }
}

/** Оставляет узлы, удовлетворяющие условию, вместе с их предками. */
export function filterTree(nodes: TreeNode[], pred: (node: TreeNode) => boolean): TreeNode[] {
  const result: TreeNode[] = [];
  for (const node of nodes) {
    const children = filterTree(node.children, pred);
    if (pred(node) || children.length) result.push({ ...node, children });
  }
  return result;
}

export function branchIds(nodes: TreeNode[]): string[] {
  const ids: string[] = [];
  walkTree(nodes, (n) => n.children.length && ids.push(n.id));
  return ids;
}

export function matchesQuery(query: string, ...values: (string | null | undefined)[]): boolean {
  const q = query.trim().toLowerCase().replace(/ё/g, "е");
  if (!q) return true;
  return values.some((v) => v && v.toLowerCase().replace(/ё/g, "е").includes(q));
}

export function problemDocuments(report: Report): DocumentInfo[] {
  return report.documents.filter((d) => d.status !== "ok");
}
