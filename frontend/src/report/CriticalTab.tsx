import { Target } from "lucide-react";
import { useMemo, useState } from "react";
import { ColumnChart, type ColumnItem, Meter } from "../components/Charts";
import { DocLink, Empty, SourceButton } from "../components/common";
import { DataTable } from "../components/DataTable";
import { formatNumber } from "../lib/format";
import { CRITICAL_RULES, PARAMETER_CATEGORY } from "../lib/labels";
import { useReport } from "./ReportContext";

export function CriticalTab() {
  const { report } = useReport();
  const [rule, setRule] = useState<string | null>(null);
  const crit = report.critical_parameters;

  const ruleCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of crit) m.set(c.rule, (m.get(c.rule) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [crit]);

  const params = useMemo(() => report.parts.flatMap((p) => p.parameters), [report.parts]);

  const raItems = useMemo<ColumnItem[]>(() => {
    const m = new Map<number, { n: number; crit: boolean }>();
    for (const p of params) {
      if (p.category !== "roughness" || p.qualifier !== "Ra" || p.general || p.value === null || p.value === undefined) continue;
      const cur = m.get(p.value) ?? { n: 0, crit: false };
      m.set(p.value, { n: cur.n + 1, crit: cur.crit || p.critical });
    }
    return [...m.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([v, { n, crit: c }]) => ({ label: formatNumber(v), value: n, emphasis: c, tip: c ? "отобрано как критичное" : undefined }));
  }, [params]);

  const itItems = useMemo<ColumnItem[]>(() => {
    const m = new Map<number, { n: number; crit: boolean }>();
    for (const p of params) {
      if (p.category !== "dimension_tolerance" || !p.it_grade) continue;
      const cur = m.get(p.it_grade) ?? { n: 0, crit: false };
      m.set(p.it_grade, { n: cur.n + 1, crit: cur.crit || p.critical });
    }
    return [...m.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([v, { n, crit: c }]) => ({ label: `IT${v}`, value: n, emphasis: c, tip: c ? "отобрано как критичное" : undefined }));
  }, [params]);

  if (!crit.length) {
    return (
      <div className="card">
        <Empty icon={<Target size={28} />} title="Критичные параметры не выделены">
          По правилам отбора ни один параметр комплекта не отмечен как критичный.
        </Empty>
      </div>
    );
  }

  const rows = rule ? crit.filter((c) => c.rule === rule) : crit;

  return (
    <div className="stack">
      <div className="grid-2">
        <div className="card">
          <div className="card-head">
            <h3>Шероховатость Ra в комплекте</h3>
            <span className="hint">указаний на чертежах, без общих требований</span>
          </div>
          <div className="card-body">
            {raItems.length ? (
              <ColumnChart items={raItems} unitLabel="Ra, мкм" emphasisLabel="критичные значения" restLabel="остальные" />
            ) : (
              <p className="muted">Значений нет</p>
            )}
          </div>
        </div>
        <div className="card">
          <div className="card-head">
            <h3>Квалитеты посадочных размеров</h3>
            <span className="hint">размеров с полем допуска</span>
          </div>
          <div className="card-body">
            {itItems.length ? (
              <ColumnChart items={itItems} unitLabel="Квалитет" emphasisLabel="критичные значения" restLabel="остальные" />
            ) : (
              <p className="muted">Значений нет</p>
            )}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Критичные параметры</h2>
          <span className="hint">{rule ? `${CRITICAL_RULES[rule]?.label ?? rule}: ${rows.length} из ${crit.length}` : `всего ${crit.length}`}</span>
        </div>
        <div className="card-body stack" style={{ gap: 10, paddingBottom: 12 }}>
          <p className="muted" style={{ fontSize: 13.5, maxWidth: 900 }}>
            Параметр отмечается, если он превышает общеинженерный порог трудоёмкости или выделяется на фоне остальных значений
            комплекта. Оценка 0–1 — приоритет для разбора технологом. Правила отбора:
          </p>
          <div className="chips" role="group" aria-label="Правило отбора">
            <button className="chip" aria-pressed={!rule} onClick={() => setRule(null)}>
              Все <span className="count">{crit.length}</span>
            </button>
            {ruleCounts.map(([r, n]) => (
              <button key={r} className="chip" aria-pressed={rule === r} onClick={() => setRule(rule === r ? null : r)} title={CRITICAL_RULES[r]?.hint}>
                {CRITICAL_RULES[r]?.label ?? r} <span className="count">{n}</span>
              </button>
            ))}
          </div>
          {rule && CRITICAL_RULES[rule] && <p className="subtle" style={{ fontSize: 13 }}>{CRITICAL_RULES[rule].hint}</p>}
        </div>
        <DataTable
          rows={rows}
          rowKey={(c) => c.id}
          initialSort={{ key: "score", dir: "desc" }}
          columns={[
            {
              key: "part",
              header: "Деталь",
              sort: (c) => c.designation,
              render: (c) => (
                <div>
                  <div className="mono nowrap">{c.designation ?? "—"}</div>
                  <div className="muted" style={{ fontSize: 12.5 }}>
                    {c.part_name}
                  </div>
                </div>
              ),
            },
            {
              key: "param",
              header: "Параметр",
              sort: (c) => c.text,
              render: (c) => (
                <div>
                  <b>{c.text}</b>
                  <div className="muted" style={{ fontSize: 12.5 }}>
                    {PARAMETER_CATEGORY[c.category]}
                  </div>
                </div>
              ),
            },
            { key: "rule", header: "Правило", sort: (c) => c.rule, render: (c) => <span className="nowrap">{CRITICAL_RULES[c.rule]?.label ?? c.rule}</span> },
            { key: "reason", header: "Обоснование", render: (c) => <span className="muted">{c.reason}</span> },
            {
              key: "score",
              header: "Оценка",
              sort: (c) => c.score,
              render: (c) => (
                <span className="score">
                  <Meter value={c.score} />
                  {formatNumber(c.score)}
                </span>
              ),
            },
            {
              key: "src",
              header: "Источник",
              render: (c) => (
                <span className="row" style={{ gap: 2, flexWrap: "nowrap" }}>
                  <SourceButton source={c.source} />
                  <DocLink docId={c.document_id} label="чертёж" />
                </span>
              ),
            },
          ]}
        />
      </div>
    </div>
  );
}
