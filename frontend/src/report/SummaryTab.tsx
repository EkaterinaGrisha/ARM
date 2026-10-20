import { CircleCheck, Files, ListTree, OctagonAlert, Target, TriangleAlert } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { BarList, Meter } from "../components/Charts";
import { DocLink, DocStatusBadge, SeverityIcon } from "../components/common";
import { formatPercent, plural } from "../lib/format";
import { DISCREPANCY_ORDER, DISCREPANCY_TYPES } from "../lib/labels";
import { useReport } from "./ReportContext";

export function SummaryTab() {
  const { report, jobId } = useReport();
  const navigate = useNavigate();
  const s = report.summary;
  const problems = report.documents.filter((d) => d.status !== "ok");
  const high = report.discrepancies.filter((d) => d.severity === "high");
  const ratio = s.completeness_ratio ?? null;
  const ok = s.documents_by_status.ok ?? 0;
  const base = `/jobs/${jobId}`;
  const kitLog = report.log.filter((l) => !l.document_id);

  return (
    <div className="stack">
      <div className="kpis">
        <div className="card kpi">
          <div className="label">
            <Files size={15} /> Документов
          </div>
          <div className="value">{s.documents_total}</div>
          <div className="sub">
            {ok} обработано{problems.length > 0 && <> · {problems.length} проблемных</>}
          </div>
        </div>
        <div className="card kpi">
          <div className="label">
            <ListTree size={15} /> Позиций в дереве
          </div>
          <div className="value">{s.tree_items}</div>
          <div className="sub">
            {s.specifications} {plural(s.specifications, "спецификация", "спецификации", "спецификаций")} · {s.drawings}{" "}
            {plural(s.drawings, "чертёж", "чертежа", "чертежей")}
          </div>
        </div>
        <div className="card kpi">
          <div className="label">
            <CircleCheck size={15} /> Укомплектованность
          </div>
          <div className="value">{formatPercent(ratio)}</div>
          {ratio !== null && <Meter value={ratio} tone={ratio < 0.9 ? (ratio < 0.7 ? "critical" : "warning") : undefined} />}
          <div className="sub">позиций, требующих чертежа, обеспечены им</div>
        </div>
        <div className="card kpi">
          <div className="label">
            <TriangleAlert size={15} /> Расхождений
          </div>
          <div className="value">{s.discrepancies_total}</div>
          <div className="sub">{high.length ? `${high.length} высокой важности` : "высокой важности нет"}</div>
        </div>
        <div className="card kpi">
          <div className="label">
            <Target size={15} /> Критичных параметров
          </div>
          <div className="value">{s.critical_parameters}</div>
          <div className="sub">
            из {s.parameters} извлечённых у {s.parts} {plural(s.parts, "чертежа", "чертежей", "чертежей")}
          </div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Расхождения по типам</h2>
            <span className="hint">нажмите, чтобы открыть список</span>
          </div>
          <div className="card-body">
            <BarList
              items={DISCREPANCY_ORDER.map((t) => ({
                key: t,
                label: DISCREPANCY_TYPES[t].label,
                value: s.discrepancies_by_type[t] ?? 0,
                hint: DISCREPANCY_TYPES[t].hint,
              }))}
              onSelect={(t) => navigate(`${base}/discrepancies?type=${t}`)}
            />
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Проблемные файлы</h2>
            <span className="hint">не прерывают обработку комплекта</span>
          </div>
          {problems.length === 0 ? (
            <div className="card-body muted">Все файлы прочитаны и разобраны.</div>
          ) : (
            <ul className="list-plain" style={{ marginTop: 8 }}>
              {problems.map((d) => (
                <li key={d.id} className="item-row">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ overflowWrap: "anywhere", fontWeight: 550 }}>{d.path}</div>
                    <div className="muted" style={{ fontSize: 13 }}>
                      {d.problem}
                    </div>
                  </div>
                  <DocStatusBadge status={d.status} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {high.length > 0 && (
        <div className="card">
          <div className="card-head" style={{ paddingBottom: 6 }}>
            <OctagonAlert size={18} style={{ color: "var(--critical-ink)" }} />
            <h2>Требует внимания в первую очередь</h2>
          </div>
          {high.slice(0, 6).map((d) => (
            <div key={d.id} className="disc">
              <SeverityIcon severity={d.severity} />
              <div style={{ minWidth: 0 }}>
                <div className="title">
                  {DISCREPANCY_TYPES[d.type].label}
                  {d.designation && <> · <span className="mono">{d.designation}</span></>}
                </div>
                <div className="msg">{d.message}</div>
              </div>
              <div className="docs" style={{ marginTop: 0, justifyContent: "flex-end" }}>
                {d.document_ids.slice(0, 2).map((id) => (
                  <DocLink key={id} docId={id} />
                ))}
              </div>
            </div>
          ))}
          {high.length > 6 && (
            <div className="card-foot">
              <Link to={`${base}/discrepancies`}>Все расхождения ({report.discrepancies.length})</Link>
            </div>
          )}
        </div>
      )}

      {kitLog.length > 0 && (
        <div className="card card-body">
          <h3 style={{ marginBottom: 8 }}>Замечания конвейера</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }} className="muted">
            {kitLog.map((l, i) => (
              <li key={i}>{l.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
