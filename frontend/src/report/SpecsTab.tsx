import { CircleCheck, CircleX } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import type { SpecRow } from "../api/types";
import { DocLink, Empty, SourceButton, ValueCell } from "../components/common";
import { extractedText } from "../lib/format";
import { useReport } from "./ReportContext";

const NEEDS_DOC = new Set(["Документация", "Сборочные единицы", "Детали", "Комплексы"]);

export function SpecsTab() {
  const { report, index } = useReport();
  const specs = report.specifications;
  const [selected, setSelected] = useState(specs[0]?.document_id ?? null);
  const spec = specs.find((s) => s.document_id === selected) ?? specs[0];

  const sections = useMemo(() => {
    const out: { section: string; rows: SpecRow[] }[] = [];
    for (const row of spec?.rows ?? []) {
      const name = row.section ?? "Без раздела";
      if (out.at(-1)?.section !== name) out.push({ section: name, rows: [] });
      out.at(-1)!.rows.push(row);
    }
    return out;
  }, [spec]);

  if (!spec) {
    return (
      <div className="card">
        <Empty title="Спецификации не найдены">Дерево изделия и проверка комплектности строятся по спецификациям комплекта.</Empty>
      </div>
    );
  }

  const presence = (row: SpecRow) => {
    const des = extractedText(row.designation);
    if (!des || !NEEDS_DOC.has(row.section ?? "")) return null;
    const docs = index.docsByDesignation.get(des) ?? [];
    const found = docs.filter((d) => d.id !== spec.document_id);
    if (found.length) return { ok: true, docs: found };
    // сборочная единица: достаточно её спецификации или СБ
    const sb = index.docsByDesignation.get(`${des} СБ`) ?? [];
    return sb.length ? { ok: true, docs: sb } : { ok: false, docs: [] };
  };

  return (
    <div className="split">
      <div className="card side-list" style={{ overflow: "hidden" }}>
        {specs.map((s) => (
          <button key={s.document_id} aria-current={s.document_id === spec.document_id} onClick={() => setSelected(s.document_id)}>
            <div className="mono">{extractedText(s.designation) || "без обозначения"}</div>
            <div className="muted" style={{ fontSize: 13 }}>
              {extractedText(s.name) || index.docs.get(s.document_id)?.file_name} · {s.rows.length} строк
            </div>
          </button>
        ))}
      </div>

      <div className="card">
        <div className="toolbar">
          <div style={{ minWidth: 0 }}>
            <h2 className="mono" style={{ fontSize: 16 }}>
              <ValueCell ex={spec.designation} mono />
            </h2>
            <div className="muted">
              <ValueCell ex={spec.name} />
            </div>
          </div>
          <span className="spacer" />
          <span className="muted" style={{ fontSize: 13 }}>
            Лит. <ValueCell ex={spec.litera} /> · Изм. <ValueCell ex={spec.revision} />
          </span>
          <DocLink docId={spec.document_id} />
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Формат</th>
                <th>Зона</th>
                <th className="num">Поз.</th>
                <th>Обозначение</th>
                <th>Наименование</th>
                <th className="num">Кол.</th>
                <th>Примечание</th>
                <th>Документ в комплекте</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sections.map(({ section, rows }) => (
                <Fragment key={section}>
                  <tr className="group">
                    <td colSpan={9}>{section}</td>
                  </tr>
                  {rows.map((row) => {
                    const p = presence(row);
                    return (
                      <tr key={row.id}>
                        <td>{extractedText(row.format)}</td>
                        <td>{extractedText(row.zone)}</td>
                        <td className="num">{extractedText(row.position)}</td>
                        <td className="mono nowrap">{extractedText(row.designation)}</td>
                        <td>{extractedText(row.name)}</td>
                        <td className="num">{extractedText(row.quantity)}</td>
                        <td>{extractedText(row.note)}</td>
                        <td>
                          {p === null ? (
                            <span className="subtle">не требуется</span>
                          ) : p.ok ? (
                            <div className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                              <CircleCheck size={15} style={{ color: "var(--good-ink)" }} aria-label="есть" />
                              {p.docs.map((d) => (
                                <DocLink key={d.id} docId={d.id} />
                              ))}
                            </div>
                          ) : (
                            <span className="badge critical">
                              <CircleX size={13} /> нет в комплекте
                            </span>
                          )}
                        </td>
                        <td>
                          <SourceButton source={row.source} />
                        </td>
                      </tr>
                    );
                  })}
                </Fragment>
              ))}
              {!sections.length && (
                <tr>
                  <td colSpan={9}>
                    <div className="empty">Строки спецификации не распознаны</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
