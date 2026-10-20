import { useMemo, useState } from "react";
import type { DocumentInfo, DocumentStatus } from "../api/types";
import { DocStatusBadge, DocStatusIcon, SearchInput } from "../components/common";
import { DataTable } from "../components/DataTable";
import { formatBytes } from "../lib/format";
import { DOCUMENT_KIND, DOCUMENT_STATUS, ORIGIN, PROBLEM_STATUSES } from "../lib/labels";
import { matchesQuery } from "../lib/report";
import { useReport } from "./ReportContext";

type Filter = "all" | "problems" | DocumentStatus;

export function DocumentsTab() {
  const { report, openDocument } = useReport();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const byStatus = useMemo(() => {
    const m: Partial<Record<DocumentStatus, DocumentInfo[]>> = {};
    for (const d of report.documents) (m[d.status] ??= []).push(d);
    return m;
  }, [report.documents]);
  const problems = report.documents.filter((d) => d.status !== "ok");

  const rows = report.documents.filter(
    (d) =>
      (filter === "all" || (filter === "problems" ? d.status !== "ok" : d.status === filter)) &&
      matchesQuery(query, d.path, d.designation, d.name, d.problem),
  );

  return (
    <div className="stack">
      {problems.length > 0 && (
        <div className="kpis" style={{ marginBottom: 0 }}>
          {PROBLEM_STATUSES.filter((s) => byStatus[s]?.length).map((s) => (
            <button
              key={s}
              className="card kpi"
              style={{ textAlign: "left", cursor: "pointer", outline: filter === s ? "2px solid var(--accent)" : undefined }}
              onClick={() => setFilter(filter === s ? "all" : s)}
            >
              <div className="label">
                <DocStatusIcon status={s} size={15} /> {DOCUMENT_STATUS[s].label}
              </div>
              <div className="value">{byStatus[s]!.length}</div>
              <div className="sub">{DOCUMENT_STATUS[s].hint}</div>
            </button>
          ))}
        </div>
      )}
      <div className="card">
        <div className="toolbar">
          <div className="chips">
            <button className="chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
              Все <span className="count">{report.documents.length}</span>
            </button>
            <button className="chip" aria-pressed={filter === "problems"} onClick={() => setFilter("problems")}>
              Проблемные <span className="count">{problems.length}</span>
            </button>
            <button className="chip" aria-pressed={filter === "ok"} onClick={() => setFilter("ok")}>
              Обработанные <span className="count">{byStatus.ok?.length ?? 0}</span>
            </button>
          </div>
          <span className="spacer" />
          <SearchInput value={query} onChange={setQuery} placeholder="Файл, обозначение" />
        </div>
        <DataTable
          rows={rows}
          rowKey={(d) => d.id}
          onRowClick={(d) => d.pages.length && openDocument(d.id)}
          initialSort={{ key: "path", dir: "asc" }}
          columns={[
            {
              key: "path",
              header: "Файл",
              sort: (d) => d.path,
              render: (d) => (
                <div style={{ minWidth: 220 }}>
                  <div style={{ fontWeight: 550, overflowWrap: "anywhere" }}>{d.file_name}</div>
                  {d.path !== d.file_name && (
                    <div className="subtle" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
                      {d.path}
                    </div>
                  )}
                </div>
              ),
            },
            { key: "kind", header: "Тип", sort: (d) => DOCUMENT_KIND[d.kind], render: (d) => <span className="nowrap">{DOCUMENT_KIND[d.kind]}</span> },
            {
              key: "origin",
              header: "Источник текста",
              sort: (d) => d.origin,
              render: (d) => {
                const ocr = d.pages.some((p) => p.has_text) && d.origin === "scan";
                return (
                  <span className="nowrap">
                    {ORIGIN[d.origin]}
                    {ocr && <span className="subtle"> · OCR</span>}
                  </span>
                );
              },
            },
            { key: "pages", header: "Стр.", className: "num", sort: (d) => d.pages.length, render: (d) => d.pages.length || "—" },
            { key: "size", header: "Размер", className: "num", sort: (d) => d.size_bytes, render: (d) => <span className="nowrap">{formatBytes(d.size_bytes)}</span> },
            { key: "des", header: "Обозначение", sort: (d) => d.designation, render: (d) => <span className="mono nowrap">{d.designation ?? "—"}</span> },
            { key: "name", header: "Наименование", sort: (d) => d.name, render: (d) => d.name ?? "—" },
            { key: "status", header: "Статус", sort: (d) => d.status, render: (d) => <DocStatusBadge status={d.status} /> },
            {
              key: "problem",
              header: "Пояснение",
              render: (d) =>
                d.problem || d.notes.length ? (
                  <div className="muted" style={{ fontSize: 13, minWidth: 220 }}>
                    {d.problem && <div>{d.problem}</div>}
                    {d.notes.map((n) => (
                      <div key={n}>{n}</div>
                    ))}
                  </div>
                ) : (
                  <span className="subtle">—</span>
                ),
            },
          ]}
        />
      </div>
    </div>
  );
}
