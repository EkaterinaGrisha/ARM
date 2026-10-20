import { FlaskConical } from "lucide-react";
import { useMemo, useState } from "react";
import type { ErrorCase, QualityMetric } from "../api/types";
import { Meter } from "../components/Charts";
import { DocLink, Empty, Segmented } from "../components/common";
import { formatPercent } from "../lib/format";
import { useReport } from "./ReportContext";

const BLOCKS: Record<QualityMetric["block"], string> = {
  completeness: "Блок 1. Контроль комплектности",
  extraction: "Блок 2. Извлечение данных",
};
const ERROR_KIND: Record<ErrorCase["kind"], string> = {
  false_positive: "Ложное срабатывание",
  false_negative: "Пропуск",
  wrong_value: "Неверное значение",
};

function Score({ v }: { v?: number | null }) {
  if (v === null || v === undefined) return <span className="subtle">—</span>;
  return (
    <span className="score" title={formatPercent(v)}>
      <Meter value={v} tone={v < 0.7 ? "critical" : v < 0.9 ? "warning" : undefined} />
      {formatPercent(v)}
    </span>
  );
}

function MetricsTable({ metrics }: { metrics: QualityMetric[] }) {
  return (
    <div className="table-wrap">
      <table className="table fixed" style={{ minWidth: 760 }}>
        <colgroup>
          <col style={{ width: "34%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "13%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "11%" }} />
        </colgroup>
        <thead>
          <tr>
            <th>Метрика</th>
            <th>Точность (precision)</th>
            <th>Полнота (recall)</th>
            <th>F1</th>
            <th>Доля верных</th>
            <th className="num">Объём эталона</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr key={m.name}>
              <td>{m.name}</td>
              <td>
                <Score v={m.precision} />
              </td>
              <td>
                <Score v={m.recall} />
              </td>
              <td>
                <Score v={m.f1} />
              </td>
              <td>
                <Score v={m.accuracy} />
              </td>
              <td className="num">{m.support ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function QualityTab() {
  const { report } = useReport();
  const q = report.quality;
  const [block, setBlock] = useState<"all" | ErrorCase["block"]>("all");
  const errors = useMemo(() => (q ? q.errors.filter((e) => block === "all" || e.block === block) : []), [q, block]);

  if (!q) {
    return (
      <div className="card">
        <Empty icon={<FlaskConical size={28} />} title="Метрики качества не рассчитаны">
          <p style={{ maxWidth: 640, margin: "0 auto" }}>
            Метрики считаются по эталонной разметке комплекта:{" "}
            <span className="mono">python -m arm_web process ПАПКА --truth эталон.json</span> или{" "}
            <span className="mono">python -m arm_web evaluate report.json эталон.json --write</span>. Пример эталона — в демо-отчёте.
          </p>
        </Empty>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="grid-2">
        <div className="card card-body">
          <h3 style={{ marginBottom: 6 }}>Эталонная выборка</h3>
          <p className="muted">{q.dataset || "—"}</p>
          <h3 style={{ margin: "14px 0 6px" }}>Методика</h3>
          <p className="muted">{q.methodology}</p>
        </div>
        <div className="card card-body">
          <h3 style={{ marginBottom: 6 }}>Ограничения решения</h3>
          <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
            {q.limitations.map((l) => (
              <li key={l} style={{ marginBottom: 4 }}>
                {l}
              </li>
            ))}
          </ul>
        </div>
      </div>

      {(["completeness", "extraction"] as const).map((b) => {
        const metrics = q.metrics.filter((m) => m.block === b);
        if (!metrics.length) return null;
        return (
          <div key={b} className="card">
            <div className="card-head" style={{ paddingBottom: 12 }}>
              <h2>{BLOCKS[b]}</h2>
            </div>
            <MetricsTable metrics={metrics} />
          </div>
        );
      })}

      <div className="card">
        <div className="toolbar">
          <h2>Разбор ошибок</h2>
          <span className="muted">{q.errors.length}</span>
          <span className="spacer" />
          <Segmented
            value={block}
            onChange={setBlock}
            options={[
              { value: "all", label: "Все" },
              { value: "completeness", label: "Комплектность" },
              { value: "extraction", label: "Извлечение" },
            ]}
          />
        </div>
        {errors.length === 0 ? (
          <div className="empty">Ошибок относительно эталона нет</div>
        ) : (
          <ul className="list-plain">
            {errors.map((e, i) => (
              <li key={i} className="item-row">
                <span className={`badge ${e.kind === "false_positive" ? "serious" : e.kind === "false_negative" ? "critical" : "warning"}`}>
                  {ERROR_KIND[e.kind]}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 550, overflowWrap: "anywhere" }}>{e.title}</div>
                  <div className="muted" style={{ fontSize: 13, overflowWrap: "anywhere" }}>
                    {e.description}
                  </div>
                </div>
                {e.document_id && <DocLink docId={e.document_id} label="документ" />}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
