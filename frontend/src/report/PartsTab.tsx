import { Target } from "lucide-react";
import { useMemo, useState } from "react";
import type { ParameterCategory, PartRecord, Parameter } from "../api/types";
import { Badge, DocLink, Empty, Segmented, SearchInput, SourceButton, ValueCell } from "../components/common";
import { DataTable } from "../components/DataTable";
import { extractedText, formatNumber, parameterValue } from "../lib/format";
import { DOCUMENT_KIND, PARAMETER_CATEGORY } from "../lib/labels";
import { matchesQuery } from "../lib/report";
import { useReport } from "./ReportContext";

type View = "title" | "params" | "compare";

function PartDetail({ part }: { part: PartRecord }) {
  return (
    <div className="grid-2" style={{ padding: "4px 0" }}>
      <div>
        <h4 style={{ marginBottom: 6 }}>Технические требования</h4>
        {part.requirements.length ? (
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            {part.requirements.map((r, i) => (
              <li key={i} style={{ marginBottom: 2 }}>
                {extractedText(r)} <SourceButton source={r.source} />
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">Не найдены</p>
        )}
      </div>
      <div>
        <h4 style={{ marginBottom: 6 }}>Извлечённые параметры</h4>
        {part.parameters.length ? (
          <div className="chips">
            {part.parameters.map((p) => (
              <span key={p.id} className={`badge ${p.critical ? "critical" : "outline"}`} title={`${PARAMETER_CATEGORY[p.category]}: ${p.text}`}>
                {p.critical && <Target size={12} />}
                {p.name}
                {p.general && <span className="subtle">(общ.)</span>}
                <SourceButton source={p.source} />
              </span>
            ))}
          </div>
        ) : (
          <p className="muted">Нет</p>
        )}
      </div>
    </div>
  );
}

function TitleBlocks({ parts }: { parts: PartRecord[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const tb = (p: PartRecord, k: keyof PartRecord["title_block"]) => extractedText(p.title_block[k]);
  return (
    <DataTable
      rows={parts}
      rowKey={(p) => p.document_id}
      onRowClick={(p) => setOpen(open === p.document_id ? null : p.document_id)}
      expanded={(p) => (open === p.document_id ? <PartDetail part={p} /> : null)}
      initialSort={{ key: "designation", dir: "asc" }}
      columns={[
        { key: "designation", header: "Обозначение", sort: (p) => p.designation, render: (p) => <ValueCell ex={p.title_block.designation} mono /> },
        { key: "name", header: "Наименование", width: 170, sort: (p) => p.name, render: (p) => <ValueCell ex={p.title_block.name} /> },
        { key: "material", header: "Материал", width: 220, sort: (p) => tb(p, "material"), render: (p) => <ValueCell ex={p.title_block.material} /> },
        { key: "mass", header: "Масса, кг", className: "num", sort: (p) => Number(String(p.title_block.mass?.value ?? "").replace(",", ".")) || null, render: (p) => <ValueCell ex={p.title_block.mass} /> },
        { key: "scale", header: "Масштаб", render: (p) => <ValueCell ex={p.title_block.scale} /> },
        { key: "litera", header: "Лит.", sort: (p) => tb(p, "litera"), render: (p) => <ValueCell ex={p.title_block.litera} /> },
        { key: "revision", header: "Изм.", className: "num", sort: (p) => Number(p.title_block.revision?.value ?? 0), render: (p) => <ValueCell ex={p.title_block.revision} /> },
        { key: "archive", header: "Арх. №", sort: (p) => tb(p, "archive_number"), render: (p) => <ValueCell ex={p.title_block.archive_number} mono /> },
        { key: "first_use", header: "Перв. примен.", sort: (p) => tb(p, "first_use"), render: (p) => <ValueCell ex={p.title_block.first_use} mono /> },
        {
          key: "params",
          header: "Параметры",
          className: "num",
          sort: (p) => p.parameters.length,
          render: (p) => {
            const crit = p.parameters.filter((x) => x.critical).length;
            return (
              <span className="nowrap">
                {p.parameters.length}
                {crit > 0 && (
                  <>
                    {" "}
                    <Badge tone="critical" title="Критичных параметров">
                      <Target size={12} />
                      {crit}
                    </Badge>
                  </>
                )}
              </span>
            );
          },
        },
        { key: "doc", header: "Документ", render: (p) => <DocLink docId={p.document_id} /> },
      ]}
    />
  );
}

const CATEGORY_ORDER: ParameterCategory[] = [
  "dimension_tolerance",
  "roughness",
  "geometric",
  "hardness",
  "heat_treatment",
  "coating",
  "general_tolerance",
  "other",
];

interface FlatParam {
  part: PartRecord;
  p: Parameter;
}

function Parameters({ parts }: { parts: PartRecord[] }) {
  const [category, setCategory] = useState<ParameterCategory | null>(null);
  const [query, setQuery] = useState("");
  const [onlyCritical, setOnlyCritical] = useState(false);
  const [hideGeneral, setHideGeneral] = useState(false);
  const all = useMemo<FlatParam[]>(() => parts.flatMap((part) => part.parameters.map((p) => ({ part, p }))), [parts]);
  const counts = useMemo(() => {
    const c: Partial<Record<ParameterCategory, number>> = {};
    for (const { p } of all) c[p.category] = (c[p.category] ?? 0) + 1;
    return c;
  }, [all]);
  const rows = all.filter(
    ({ part, p }) =>
      (!category || p.category === category) &&
      (!onlyCritical || p.critical) &&
      (!hideGeneral || !p.general) &&
      matchesQuery(query, part.designation, part.name, p.name, p.text, p.qualifier),
  );
  return (
    <>
      <div className="toolbar" style={{ alignItems: "flex-start" }}>
        <div className="chips" style={{ flex: "1 1 520px" }}>
          <button className="chip" aria-pressed={!category} onClick={() => setCategory(null)}>
            Все <span className="count">{all.length}</span>
          </button>
          {CATEGORY_ORDER.filter((c) => counts[c]).map((c) => (
            <button key={c} className="chip" aria-pressed={category === c} onClick={() => setCategory(category === c ? null : c)}>
              {PARAMETER_CATEGORY[c]} <span className="count">{counts[c]}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="toolbar">
        <SearchInput value={query} onChange={setQuery} placeholder="Деталь, параметр, текст" />
        <label className="check">
          <input type="checkbox" checked={onlyCritical} onChange={(e) => setOnlyCritical(e.target.checked)} />
          Только критичные
        </label>
        <label className="check">
          <input type="checkbox" checked={hideGeneral} onChange={(e) => setHideGeneral(e.target.checked)} />
          Скрыть общие требования
        </label>
        <span className="spacer" />
        <span className="muted">Показано: {rows.length}</span>
      </div>
      <DataTable
        rows={rows}
        rowKey={({ p }) => p.id}
        columns={[
          { key: "des", header: "Обозначение", sort: ({ part }) => part.designation, render: ({ part }) => <span className="mono nowrap">{part.designation ?? "—"}</span> },
          { key: "part", header: "Деталь", sort: ({ part }) => part.name, render: ({ part }) => part.name ?? "—" },
          { key: "cat", header: "Категория", sort: ({ p }) => PARAMETER_CATEGORY[p.category], render: ({ p }) => <span className="muted nowrap">{PARAMETER_CATEGORY[p.category]}</span> },
          {
            key: "name",
            header: "Параметр",
            sort: ({ p }) => p.name,
            render: ({ p }) => (
              <span className="row" style={{ gap: 6 }}>
                <b>{p.name}</b>
                {p.critical && (
                  <Badge tone="critical" title="Критичный параметр">
                    <Target size={12} />
                  </Badge>
                )}
                {p.general && <Badge title="Общее требование (неуказанные отклонения, шероховатость остального)">общее</Badge>}
              </span>
            ),
          },
          { key: "value", header: "Значение", className: "num", sort: ({ p }) => p.value ?? p.value_min ?? null, render: ({ p }) => <span className="nowrap">{parameterValue(p)}</span> },
          { key: "q", header: "Поле допуска / шкала", render: ({ p }) => p.qualifier ?? "—" },
          { key: "it", header: "IT", className: "num", sort: ({ p }) => p.it_grade ?? null, render: ({ p }) => (p.it_grade ? `IT${p.it_grade}` : "—") },
          { key: "text", header: "Исходный текст", render: ({ p }) => <span className="muted">{p.text}</span> },
          { key: "src", header: "", render: ({ p }) => <SourceButton source={p.source} /> },
        ]}
      />
    </>
  );
}

function minBy<T>(items: T[], get: (t: T) => number | null | undefined): T | undefined {
  let best: T | undefined;
  let bestV = Infinity;
  for (const it of items) {
    const v = get(it);
    if (v !== null && v !== undefined && v < bestV) {
      best = it;
      bestV = v;
    }
  }
  return best;
}

/** Ключевые параметры деталей в одной строке — для сравнения и выгрузки в учётные системы. */
function Compare({ parts }: { parts: PartRecord[] }) {
  const rows = parts
    .filter((p) => p.kind !== "assembly_drawing")
    .map((part) => {
      const ps = part.parameters;
      const of = (c: ParameterCategory) => ps.filter((p) => p.category === c);
      const ra = minBy(of("roughness").filter((p) => p.qualifier === "Ra" && !p.general), (p) => p.value);
      const fit = minBy(of("dimension_tolerance"), (p) => p.it_grade);
      const geo = minBy(of("geometric"), (p) => p.value);
      const hard = of("hardness");
      return { part, ra, fit, geo, hard, heat: of("heat_treatment"), coat: of("coating"), crit: ps.filter((p) => p.critical).length };
    });
  type R = (typeof rows)[number];
  const cell = (p: Parameter | undefined, text?: string) =>
    p ? (
      <span className="value-cell">
        <span className="nowrap" style={p.critical ? { fontWeight: 650 } : undefined} title={p.critical ? "Критичный параметр" : undefined}>
          {text ?? p.name}
        </span>
        <SourceButton source={p.source} />
      </span>
    ) : (
      <span className="subtle">—</span>
    );
  return (
    <DataTable<R>
      rows={rows}
      rowKey={(r) => r.part.document_id}
      initialSort={{ key: "des", dir: "asc" }}
      columns={[
        { key: "des", header: "Обозначение", sort: (r) => r.part.designation, render: (r) => <span className="mono nowrap">{r.part.designation ?? "—"}</span> },
        { key: "name", header: "Наименование", sort: (r) => r.part.name, render: (r) => r.part.name ?? "—" },
        { key: "mat", header: "Материал", sort: (r) => extractedText(r.part.title_block.material), render: (r) => <ValueCell ex={r.part.title_block.material} /> },
        { key: "heat", header: "Термообработка", render: (r) => (r.heat.length ? r.heat.map((h) => <div key={h.id}>{cell(h)}</div>) : <span className="subtle">—</span>) },
        { key: "hard", header: "Твёрдость", render: (r) => (r.hard.length ? r.hard.map((h) => <div key={h.id}>{cell(h, parameterValue(h))}</div>) : <span className="subtle">—</span>) },
        { key: "coat", header: "Покрытие", render: (r) => (r.coat.length ? r.coat.map((h) => <div key={h.id}>{cell(h, h.qualifier ?? h.name)}</div>) : <span className="subtle">—</span>) },
        { key: "ra", header: "Мин. Ra, мкм", className: "num", sort: (r) => r.ra?.value ?? null, render: (r) => cell(r.ra, r.ra ? formatNumber(r.ra.value) : undefined) },
        { key: "it", header: "Точнейший квалитет", sort: (r) => r.fit?.it_grade ?? null, render: (r) => cell(r.fit, r.fit ? `${r.fit.name}${r.fit.it_grade ? ` (IT${r.fit.it_grade})` : ""}` : undefined) },
        { key: "geo", header: "Мин. допуск формы, мм", className: "num", sort: (r) => r.geo?.value ?? null, render: (r) => cell(r.geo, r.geo ? formatNumber(r.geo.value) : undefined) },
        {
          key: "crit",
          header: "Критичных",
          className: "num",
          sort: (r) => r.crit,
          render: (r) => (r.crit ? <Badge tone="critical">{r.crit}</Badge> : <span className="subtle">0</span>),
        },
      ]}
    />
  );
}

export function PartsTab() {
  const { report } = useReport();
  const [view, setView] = useState<View>("title");
  const parts = report.parts;
  if (!parts.length) {
    return (
      <div className="card">
        <Empty title="Чертежи не разобраны">В комплекте не найдено чертежей с основной надписью.</Empty>
      </div>
    );
  }
  const kinds = new Set(parts.map((p) => p.kind));
  return (
    <div className="card">
      <div className="toolbar">
        <Segmented<View>
          value={view}
          onChange={setView}
          options={[
            { value: "title", label: "Основные надписи" },
            { value: "params", label: "Технические требования" },
            { value: "compare", label: "Сравнение деталей" },
          ]}
        />
        <span className="spacer" />
        <span className="muted" style={{ fontSize: 13 }}>
          {parts.length} чертежей ({[...kinds].map((k) => DOCUMENT_KIND[k].toLowerCase()).join(", ")}) · значения с пунктирным подчёркиванием
          требуют проверки
        </span>
      </div>
      {view === "title" && <TitleBlocks parts={parts} />}
      {view === "params" && <Parameters parts={parts} />}
      {view === "compare" && <Compare parts={parts} />}
    </div>
  );
}
