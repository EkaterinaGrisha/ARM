import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { Fragment, type ReactNode, useMemo, useState } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Значение для сортировки; без него колонка не сортируется. */
  sort?: (row: T) => string | number | null | undefined;
  className?: string;
  /** Минимальная ширина колонки, px. */
  width?: number;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string;
  /** Раскрываемая строка с подробностями. */
  expanded?: (row: T) => ReactNode | null;
  empty?: ReactNode;
  initialSort?: { key: string; dir: "asc" | "desc" };
}

const collator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

export function DataTable<T>({ rows, columns, rowKey, onRowClick, rowClassName, expanded, empty, initialSort }: Props<T>) {
  const [sort, setSort] = useState(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    const get = col.sort;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = get(a);
      const vb = get(b);
      if (va === vb) return 0;
      if (va === null || va === undefined || va === "") return 1;
      if (vb === null || vb === undefined || vb === "") return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return collator.compare(String(va), String(vb)) * dir;
    });
  }, [rows, columns, sort]);

  const toggle = (key: string) =>
    setSort((s) => (s?.key !== key ? { key, dir: "asc" } : s.dir === "asc" ? { key, dir: "desc" } : null));

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.className}
                aria-sort={sort?.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}>
                {c.width && <div style={{ width: c.width, height: 0 }} aria-hidden="true" />}
                {c.sort ? (
                  <button type="button" onClick={() => toggle(c.key)}>
                    {c.header}
                    {sort?.key === c.key ? (
                      sort.dir === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />
                    ) : (
                      <ChevronsUpDown size={13} opacity={0.45} />
                    )}
                  </button>
                ) : (
                  c.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const detail = expanded?.(row);
            return (
              <Fragment key={rowKey(row)}>
                <tr
                  className={`${onRowClick ? "clickable" : ""} ${detail ? "selected" : ""} ${rowClassName?.(row) ?? ""}`}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                >
                  {columns.map((c) => (
                    <td key={c.key} className={c.className}>
                      {c.render(row)}
                    </td>
                  ))}
                </tr>
                {detail && (
                  <tr className="detail">
                    <td colSpan={columns.length}>{detail}</td>
                  </tr>
                )}
              </Fragment>
            );
          })}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length}>{empty ?? <div className="empty">Нет данных</div>}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
