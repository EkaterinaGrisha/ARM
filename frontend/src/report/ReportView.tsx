import { ArrowLeft, Download, FileJson, FileSpreadsheet, Printer } from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink, useParams } from "react-router-dom";
import { DEMO_ID, exportUrl, LOCAL_ID } from "../api/client";
import type { Report } from "../api/types";
import { DocumentViewer } from "../components/DocumentViewer";
import { formatDate, formatDuration } from "../lib/format";
import { CriticalTab } from "./CriticalTab";
import { DiscrepanciesTab } from "./DiscrepanciesTab";
import { DocumentsTab } from "./DocumentsTab";
import { PartsTab } from "./PartsTab";
import { QualityTab } from "./QualityTab";
import { ReportProvider } from "./ReportContext";
import { SpecsTab } from "./SpecsTab";
import { SummaryTab } from "./SummaryTab";
import { TreeTab } from "./TreeTab";

interface TabDef {
  id: string;
  label: string;
  count?: number;
  alert?: boolean;
  /** Дополнительный красный счётчик (например, число проблемных файлов). */
  extra?: { value: number; title: string };
  render: () => ReactNode;
}

function downloadJson(report: Report) {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `Отчёт ${report.kit.name}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function ReportView({ jobId, report }: { jobId: string; report: Report }) {
  const { tab = "summary" } = useParams();
  const s = report.summary;
  const problems = report.documents.filter((d) => d.status !== "ok").length;
  const tabs: TabDef[] = [
    { id: "summary", label: "Сводка", render: () => <SummaryTab /> },
    { id: "tree", label: "Дерево изделия", count: s.tree_items, render: () => <TreeTab /> },
    { id: "discrepancies", label: "Расхождения", count: s.discrepancies_total, alert: s.discrepancies_total > 0, render: () => <DiscrepanciesTab /> },
    { id: "parts", label: "Параметры деталей", count: report.parts.length, render: () => <PartsTab /> },
    { id: "critical", label: "Критичные параметры", count: s.critical_parameters, render: () => <CriticalTab /> },
    { id: "specs", label: "Спецификации", count: report.specifications.length, render: () => <SpecsTab /> },
    {
      id: "documents",
      label: "Документы",
      count: report.documents.length,
      extra: problems ? { value: problems, title: "проблемных файлов" } : undefined,
      render: () => <DocumentsTab />,
    },
    { id: "quality", label: "Качество", render: () => <QualityTab /> },
  ];
  const current = tabs.find((t) => t.id === tab) ?? tabs[0];
  const xlsx = exportUrl(jobId, "xlsx");
  const html = exportUrl(jobId, "html");
  const json = exportUrl(jobId, "json");
  const base = `/jobs/${jobId}`;

  return (
    <ReportProvider jobId={jobId} report={report}>
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ marginBottom: 6, gap: 6 }}>
            <Link to="/" className="muted row" style={{ gap: 4, fontSize: 13 }}>
              <ArrowLeft size={14} /> Проверки
            </Link>
            {jobId === DEMO_ID && <span className="badge accent">Демо</span>}
            {jobId === LOCAL_ID && <span className="badge">Из файла</span>}
          </div>
          <h1>{report.kit.name}</h1>
          <div className="muted" style={{ marginTop: 4 }}>
            {report.kit.root_designation ? (
              <>
                Изделие <span className="mono">{report.kit.root_designation}</span> {report.kit.root_name}
              </>
            ) : (
              "Изделие верхнего уровня не определено"
            )}
            <span className="subtle">
              {" "}
              · обработан {formatDate(report.kit.processed_at)} за {formatDuration(report.kit.duration_sec)} · конвейер{" "}
              {report.kit.pipeline_name} {report.kit.pipeline_version}
            </span>
          </div>
        </div>
        <div className="row">
          {json ? (
            <a className="btn" href={json} download={jobId === DEMO_ID ? "report.json" : undefined} title="Машиночитаемый отчёт для интеграции">
              <FileJson size={16} /> JSON
            </a>
          ) : (
            <button className="btn" onClick={() => downloadJson(report)}>
              <FileJson size={16} /> JSON
            </button>
          )}
          {xlsx && (
            <a className="btn" href={xlsx} download={jobId === DEMO_ID ? "report.xlsx" : undefined} title="Таблицы для технолога">
              <FileSpreadsheet size={16} /> Excel
            </a>
          )}
          {html && (
            <a className="btn" href={html} target="_blank" rel="noreferrer" title="Печатная форма отчёта">
              <Printer size={16} /> Печать
            </a>
          )}
          {!xlsx && (
            <span className="subtle" style={{ fontSize: 12.5 }} title="Excel и печатная форма формируются сервером">
              <Download size={13} /> Excel — через сервер
            </span>
          )}
        </div>
      </div>

      <nav className="tabs" aria-label="Разделы отчёта">
        {tabs.map((t) => (
          <NavLink key={t.id} to={t.id === "summary" ? base : `${base}/${t.id}`} end className={() => (t.id === current.id ? "active" : "")}>
            {t.label}
            {t.count !== undefined && <span className={`count ${t.alert ? "alert" : ""}`}>{t.count}</span>}
            {t.extra && (
              <span className="count alert" title={t.extra.title}>
                {t.extra.value}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {current.render()}
      <DocumentViewer />
      <p className="footer-note">
        Формат отчёта {report.schema_version} · {report.documents.length} документов · идентификатор {report.kit.id}
      </p>
    </ReportProvider>
  );
}
