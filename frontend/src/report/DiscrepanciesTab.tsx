import { CircleCheck, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DiscrepancyType, Severity } from "../api/types";
import { DocLink, Empty, SearchInput, SeverityIcon, SourceButton } from "../components/common";
import { DISCREPANCY_ORDER, DISCREPANCY_TYPES, SEVERITY } from "../lib/labels";
import { matchesQuery } from "../lib/report";
import { useReport } from "./ReportContext";

const SEVERITY_ORDER: Record<Severity, number> = { high: 0, medium: 1, low: 2 };

export function DiscrepanciesTab() {
  const { report, index } = useReport();
  const [params, setParams] = useSearchParams();
  const type = params.get("type") as DiscrepancyType | null;
  const nodeId = params.get("node");
  const node = nodeId ? index.nodes.get(nodeId) : undefined;
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<Severity | "all">("all");

  const setType = (t: DiscrepancyType | null) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (t) next.set("type", t);
      else next.delete("type");
      return next;
    });

  const counts = useMemo(() => {
    const c: Partial<Record<DiscrepancyType, number>> = {};
    for (const d of report.discrepancies) c[d.type] = (c[d.type] ?? 0) + 1;
    return c;
  }, [report.discrepancies]);

  const list = useMemo(
    () =>
      report.discrepancies
        .filter((d) => (!type || d.type === type) && (severity === "all" || d.severity === severity))
        .filter((d) => !nodeId || d.tree_node_ids.includes(nodeId))
        .filter((d) =>
          matchesQuery(query, d.designation, d.message, d.title, d.rule_id, ...d.document_ids.map((id) => index.docs.get(id)?.path)),
        )
        .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]),
    [report.discrepancies, type, severity, nodeId, query, index],
  );

  if (!report.discrepancies.length) {
    return (
      <div className="card">
        <Empty icon={<CircleCheck size={28} />} title="Расхождений не найдено">
          Все позиции спецификаций обеспечены чертежами, обозначения и наименования согласованы.
        </Empty>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="chips" role="group" aria-label="Тип расхождения">
        <button className="chip" aria-pressed={!type} onClick={() => setType(null)}>
          Все <span className="count">{report.discrepancies.length}</span>
        </button>
        {DISCREPANCY_ORDER.filter((t) => counts[t]).map((t) => (
          <button key={t} className="chip" aria-pressed={type === t} onClick={() => setType(type === t ? null : t)} title={DISCREPANCY_TYPES[t].hint}>
            {DISCREPANCY_TYPES[t].label} <span className="count">{counts[t]}</span>
          </button>
        ))}
      </div>

      <div className="card">
        <div className="toolbar">
          <SearchInput value={query} onChange={setQuery} placeholder="Обозначение, файл, текст, правило" />
          <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value as Severity | "all")} aria-label="Важность">
            <option value="all">Любая важность</option>
            {(Object.keys(SEVERITY) as Severity[]).map((s) => (
              <option key={s} value={s}>
                {SEVERITY[s]}
              </option>
            ))}
          </select>
          {node && (
            <span className="badge accent">
              Позиция <span className="mono">{node.designation ?? node.name}</span>
              <button
                className="src-btn"
                style={{ width: 18, height: 18 }}
                aria-label="Сбросить фильтр позиции"
                onClick={() =>
                  setParams((prev) => {
                    const next = new URLSearchParams(prev);
                    next.delete("node");
                    return next;
                  })
                }
              >
                <X size={12} />
              </button>
            </span>
          )}
          <span className="spacer" />
          <span className="muted">Показано: {list.length}</span>
        </div>

        {list.map((d) => (
          <div key={d.id} className="disc">
            <SeverityIcon severity={d.severity} />
            <div style={{ minWidth: 0 }}>
              <div className="row" style={{ gap: 8 }}>
                <span className="title">{DISCREPANCY_TYPES[d.type]?.label ?? d.type}</span>
                {d.designation && <span className="mono">{d.designation}</span>}
                <span className="subtle" style={{ fontSize: 12.5 }}>
                  {d.title}
                </span>
                {d.rule_id && (
                  <span className="mono subtle" style={{ fontSize: 11.5 }} title="Правило проверки">
                    {d.rule_id}
                  </span>
                )}
              </div>
              <div className="msg">{d.message}</div>
              {(d.expected || d.actual) && (
                <dl className="diff">
                  {d.expected && (
                    <>
                      <dt>Ожидалось</dt>
                      <dd>{d.expected}</dd>
                    </>
                  )}
                  {d.actual && (
                    <>
                      <dt>Фактически</dt>
                      <dd style={{ overflowWrap: "anywhere" }}>{d.actual}</dd>
                    </>
                  )}
                </dl>
              )}
              {d.document_ids.length > 0 && (
                <div className="docs">
                  {d.document_ids.map((id) => (
                    <DocLink key={id} docId={id} />
                  ))}
                </div>
              )}
            </div>
            <div className="row" style={{ alignItems: "flex-start", gap: 0 }} title="Источники значений">
              {d.sources.map((s, i) => (
                <SourceButton key={i} source={s} />
              ))}
            </div>
          </div>
        ))}
        {list.length === 0 && <div className="empty">Нет расхождений по выбранным условиям</div>}
      </div>
    </div>
  );
}
