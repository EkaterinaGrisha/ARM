import {
  Ban,
  CircleAlert,
  CircleCheck,
  CircleX,
  Crosshair,
  FileLock,
  FileQuestion,
  Files,
  FileText,
  FileWarning,
  FileX,
  Info,
  OctagonAlert,
  ScanLine,
  TriangleAlert,
} from "lucide-react";
import type { ReactNode } from "react";
import type { DocumentInfo, DocumentStatus, Extracted, NodeStatus, Severity, SourceRef } from "../api/types";
import { extractedText, formatPercent } from "../lib/format";
import { DOCUMENT_STATUS, METHOD, NODE_STATUS, SEVERITY } from "../lib/labels";
import { useReport } from "../report/ReportContext";

export function Badge({ tone, children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone ?? ""}`} title={title}>
      {children}
    </span>
  );
}

const DOC_STATUS_TONE: Record<DocumentStatus, { tone: string; icon: ReactNode }> = {
  ok: { tone: "good", icon: <CircleCheck size={13} /> },
  warning: { tone: "warning", icon: <TriangleAlert size={13} /> },
  damaged: { tone: "critical", icon: <FileX size={13} /> },
  unreadable: { tone: "serious", icon: <ScanLine size={13} /> },
  foreign: { tone: "", icon: <FileQuestion size={13} /> },
  unsupported: { tone: "", icon: <Ban size={13} /> },
  encrypted: { tone: "serious", icon: <FileLock size={13} /> },
  duplicate_file: { tone: "", icon: <Files size={13} /> },
};

export function DocStatusBadge({ status }: { status: DocumentStatus }) {
  const s = DOC_STATUS_TONE[status];
  return (
    <Badge tone={s.tone} title={DOCUMENT_STATUS[status].hint}>
      {s.icon}
      {DOCUMENT_STATUS[status].label}
    </Badge>
  );
}

export function DocStatusIcon({ status, size = 16 }: { status: DocumentStatus; size?: number }) {
  const map: Record<DocumentStatus, ReactNode> = {
    ok: <FileText size={size} />,
    warning: <FileWarning size={size} />,
    damaged: <FileX size={size} />,
    unreadable: <ScanLine size={size} />,
    foreign: <FileQuestion size={size} />,
    unsupported: <Ban size={size} />,
    encrypted: <FileLock size={size} />,
    duplicate_file: <Files size={size} />,
  };
  return <>{map[status]}</>;
}

const NODE_TONE: Record<NodeStatus, { tone: string; icon: ReactNode }> = {
  ok: { tone: "good", icon: <CircleCheck size={13} /> },
  missing: { tone: "critical", icon: <CircleX size={13} /> },
  warning: { tone: "warning", icon: <TriangleAlert size={13} /> },
  not_required: { tone: "", icon: null },
};

export function NodeStatusBadge({ status }: { status: NodeStatus }) {
  const s = NODE_TONE[status];
  if (status === "not_required") return <span className="subtle">не требуется</span>;
  return (
    <Badge tone={s.tone}>
      {s.icon}
      {NODE_STATUS[status]}
    </Badge>
  );
}

export function SeverityIcon({ severity, size = 16 }: { severity: Severity; size?: number }) {
  const icon = severity === "high" ? <OctagonAlert size={size} /> : severity === "medium" ? <TriangleAlert size={size} /> : <Info size={size} />;
  return (
    <span className={`sev ${severity}`} title={`Важность: ${SEVERITY[severity].toLowerCase()}`} role="img" aria-label={SEVERITY[severity]}>
      {icon}
    </span>
  );
}

export function sourceTitle(src: SourceRef, doc?: DocumentInfo): string {
  const parts = [doc?.path ?? src.document_id, `стр. ${src.page}`];
  if (src.method) parts.push(METHOD[src.method] ?? src.method);
  if (src.confidence !== null && src.confidence !== undefined) parts.push(`уверенность ${formatPercent(src.confidence)}`);
  return `Показать в документе: ${parts.join(" · ")}`;
}

/** Кнопка «показать источник»: открывает документ и подсвечивает область страницы. */
export function SourceButton({ source }: { source?: SourceRef | null }) {
  const { openSource, index } = useReport();
  if (!source) return null;
  return (
    <button
      type="button"
      className="src-btn"
      title={sourceTitle(source, index.docs.get(source.document_id))}
      aria-label="Показать источник"
      onClick={(e) => {
        e.stopPropagation();
        openSource(source);
      }}
    >
      <Crosshair size={14} />
    </button>
  );
}

/** Значение требует проверки: так его отметило ядро (статус значения, с версии 1.2 отчёта). */
export function needsReview(ex?: Extracted | null): boolean {
  return ex?.status === "low_confidence" || ex?.status === "conflict";
}

function reviewHint(ex: Extracted): string {
  if (ex.status === "conflict") return "Источники дают разные значения — проверьте по документу";
  const method = ex.source?.method;
  return `Получено: ${method ? METHOD[method] : "с низкой уверенностью"} — проверьте по документу`;
}

/** Извлечённое значение + ссылка на источник; сомнительные значения подчёркнуты. */
export function ValueCell({ ex, mono = false, empty = "—" }: { ex?: Extracted | null; mono?: boolean; empty?: string }) {
  const text = extractedText(ex);
  if (!ex || !text) return <span className="subtle">{empty}</span>;
  const low = needsReview(ex);
  return (
    <span className="value-cell">
      <span className={`v ${mono ? "mono" : ""} ${low ? "lowconf" : ""}`} title={low ? reviewHint(ex) : undefined}>
        {text}
      </span>
      <SourceButton source={ex?.source} />
    </span>
  );
}

export function DocLink({ docId, page = 1, label }: { docId: string; page?: number; label?: string }) {
  const { index, openDocument } = useReport();
  const doc = index.docs.get(docId);
  if (!doc) return <span className="subtle">{docId}</span>;
  const viewable = doc.pages.length > 0;
  return (
    <button
      type="button"
      className="doc-link"
      title={viewable ? `Открыть ${doc.path}` : `${doc.path} — просмотр недоступен`}
      disabled={!viewable}
      onClick={(e) => {
        e.stopPropagation();
        openDocument(docId, page);
      }}
    >
      <DocStatusIcon status={doc.status} size={13} />
      <span>{label ?? doc.file_name}</span>
    </button>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <CircleAlert size={28} />}
      <h3>{title}</h3>
      {children && <div>{children}</div>}
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <label className="search">
      <span className="sr-only">{placeholder}</span>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
      <input className="input" type="search" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="segmented" role="group">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
