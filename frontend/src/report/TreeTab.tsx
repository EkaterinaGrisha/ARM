import { Box, Boxes, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Cog, FileText, Package, Droplet, ListTree } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { NodeKind, TreeNode } from "../api/types";
import { Badge, DocLink, Empty, NodeStatusBadge, SearchInput } from "../components/common";
import { formatNumber, plural } from "../lib/format";
import { NODE_KIND } from "../lib/labels";
import { branchIds, filterTree, matchesQuery, walkTree } from "../lib/report";
import { useReport } from "./ReportContext";

const KIND_ICON: Record<NodeKind, ReactNode> = {
  kit: <Boxes size={15} />,
  assembly: <Boxes size={15} />,
  part: <Cog size={15} />,
  standard: <Package size={15} />,
  other_item: <Box size={15} />,
  material: <Droplet size={15} />,
  document: <FileText size={15} />,
  unknown: <Box size={15} />,
};

interface Row {
  node: TreeNode;
  depth: number;
}

export function TreeTab() {
  const { report, jobId } = useReport();
  const [query, setQuery] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [hideStandard, setHideStandard] = useState(false);
  const allBranches = useMemo(() => branchIds(report.tree), [report.tree]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const filtered = useMemo(() => {
    const active = query.trim() || onlyIssues || hideStandard;
    if (!active) return report.tree;
    return filterTree(report.tree, (n) => {
      if (hideStandard && (n.kind === "standard" || n.kind === "material" || n.kind === "other_item")) return false;
      if (onlyIssues && n.status !== "missing" && n.status !== "warning") return false;
      return matchesQuery(query, n.designation, n.name);
    });
  }, [report.tree, query, onlyIssues, hideStandard]);

  const filtering = Boolean(query.trim() || onlyIssues || hideStandard);
  const rows = useMemo(() => {
    const out: Row[] = [];
    const visit = (nodes: TreeNode[], depth: number) => {
      for (const node of nodes) {
        out.push({ node, depth });
        if (node.children.length && (filtering || !collapsed.has(node.id))) visit(node.children, depth + 1);
      }
    };
    visit(filtered, 0);
    return out;
  }, [filtered, collapsed, filtering]);

  const stats = useMemo(() => {
    let missing = 0;
    let warning = 0;
    walkTree(report.tree, (n) => {
      if (n.status === "missing") missing++;
      if (n.status === "warning") warning++;
    });
    return { missing, warning };
  }, [report.tree]);

  if (!report.tree.length) {
    return (
      <div className="card">
        <Empty icon={<ListTree size={28} />} title="Дерево изделия не построено">
          В комплекте не найдено спецификаций. Проверьте вкладку «Документы».
        </Empty>
      </div>
    );
  }

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="card">
      <div className="toolbar">
        <SearchInput value={query} onChange={setQuery} placeholder="Обозначение или наименование" />
        <label className="check">
          <input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} />
          Только с замечаниями
        </label>
        <label className="check">
          <input type="checkbox" checked={hideStandard} onChange={(e) => setHideStandard(e.target.checked)} />
          Скрыть стандартные изделия и материалы
        </label>
        <span className="spacer" />
        <span className="muted" style={{ fontSize: 13 }}>
          {stats.missing > 0 && (
            <>
              <Badge tone="critical">{stats.missing}</Badge> без документа{" "}
            </>
          )}
          {stats.warning > 0 && (
            <>
              <Badge tone="warning">{stats.warning}</Badge> с замечаниями
            </>
          )}
        </span>
        <button className="btn sm ghost" onClick={() => setCollapsed(new Set())} disabled={filtering} title="Развернуть всё">
          <ChevronsUpDown size={15} /> Развернуть
        </button>
        <button className="btn sm ghost" onClick={() => setCollapsed(new Set(allBranches))} disabled={filtering} title="Свернуть всё">
          <ChevronsDownUp size={15} /> Свернуть
        </button>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th style={{ minWidth: 280 }}>Обозначение</th>
              <th style={{ minWidth: 200 }}>Наименование</th>
              <th>Раздел</th>
              <th className="num">Поз.</th>
              <th className="num">Кол.</th>
              <th className="num" title="Количество на изделие верхнего уровня">На изделие</th>
              <th>Документы</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ node, depth }) => {
              const isOpen = filtering || !collapsed.has(node.id);
              const docs = [...(node.spec_document_id ? [node.spec_document_id] : []), ...node.drawing_document_ids];
              return (
                <tr key={node.id} className={node.status === "missing" ? "tree-row-missing" : node.status === "warning" ? "tree-row-warning" : ""}>
                  <td>
                    <div className="tree-cell" style={{ paddingLeft: depth * 20 }}>
                      <button
                        className={`tree-toggle ${node.children.length ? "" : "leaf"}`}
                        onClick={() => toggle(node.id)}
                        aria-label={isOpen ? "Свернуть" : "Развернуть"}
                        aria-expanded={node.children.length ? isOpen : undefined}
                      >
                        {isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                      </button>
                      <span className="kind-icon" title={NODE_KIND[node.kind]}>
                        {KIND_ICON[node.kind]}
                      </span>
                      {node.designation ? <span className="mono">{node.designation}</span> : <span className="subtle">—</span>}
                      {node.children.length > 0 && !isOpen && (
                        <span className="subtle" style={{ fontSize: 12 }}>
                          ({node.children.length})
                        </span>
                      )}
                    </div>
                  </td>
                  <td style={{ fontWeight: node.kind === "assembly" || node.kind === "kit" ? 600 : undefined }}>{node.name ?? "—"}</td>
                  <td className="muted nowrap">{node.section ?? NODE_KIND[node.kind]}</td>
                  <td className="num">{node.position ?? ""}</td>
                  <td className="num">{node.quantity !== null && node.quantity !== undefined ? formatNumber(node.quantity) : ""}</td>
                  <td className="num">{node.total_quantity !== null && node.total_quantity !== undefined ? formatNumber(node.total_quantity) : ""}</td>
                  <td>
                    <div className="row" style={{ gap: 4 }}>
                      {docs.map((id) => (
                        <DocLink key={id} docId={id} />
                      ))}
                    </div>
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <NodeStatusBadge status={node.status} />
                      {node.discrepancy_ids.length > 0 && (
                        <Link
                          to={`/jobs/${jobId}/discrepancies?node=${node.id}`}
                          className="nowrap"
                          style={{ fontSize: 12.5 }}
                          title="Показать расхождения позиции"
                        >
                          {node.discrepancy_ids.length} {plural(node.discrepancy_ids.length, "замечание", "замечания", "замечаний")}
                        </Link>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8}>
                  <div className="empty">Ничего не найдено</div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
