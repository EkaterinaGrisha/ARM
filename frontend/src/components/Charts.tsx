import { useEffect, useRef, useState } from "react";
import { formatNumber, formatPercent } from "../lib/format";

export interface BarItem {
  key: string;
  label: string;
  value: number;
  hint?: string;
}

/** Горизонтальные столбцы одной серии; значение подписано у конца столбца. */
export function BarList({ items, onSelect }: { items: BarItem[]; onSelect?: (key: string) => void }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="barlist" role="list">
      {items.map((item) => (
        <button
          type="button"
          role="listitem"
          key={item.key}
          className="barlist-row"
          title={item.hint ? `${item.label}: ${item.value} — ${item.hint}` : `${item.label}: ${item.value}`}
          onClick={() => onSelect?.(item.key)}
        >
          <span className="name">{item.label}</span>
          <span className="track">
            <span className={`bar ${item.value === 0 ? "zero" : ""}`} style={{ width: `${(item.value / max) * 100}%`, display: "block" }} />
          </span>
          <span className="val">{item.value}</span>
        </button>
      ))}
    </div>
  );
}

export interface ColumnItem {
  label: string;
  value: number;
  emphasis?: boolean;
  tip?: string;
}

const H = 190;
const PAD = { top: 12, right: 8, bottom: 30, left: 30 };

function niceMax(v: number): number {
  if (v <= 4) return Math.max(1, Math.ceil(v));
  const step = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= v) return m * step;
  return 10 * step;
}

function topRounded(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/**
 * Распределение значений в комплекте (одна величина — один столбец).
 * Выделенные столбцы — акцентный цвет, остальные — нейтральный; подписи — в легенде и подсказке.
 */
export function ColumnChart({
  items,
  emphasisLabel,
  restLabel,
  unitLabel,
}: {
  items: ColumnItem[];
  emphasisLabel: string;
  restLabel: string;
  unitLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  // ширина в пикселях: подписи осей остаются читаемыми при любом размере карточки
  const ref = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(560);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setW(Math.max(240, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const total = items.reduce((s, i) => s + i.value, 0);
  const max = niceMax(Math.max(...items.map((i) => i.value), 1));
  const ticks = max <= 4 ? Array.from({ length: max + 1 }, (_, i) => i) : [0, max / 2, max];
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const slot = plotW / Math.max(1, items.length);
  const barW = Math.min(24, slot * 0.6);
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const hovered = hover !== null ? items[hover] : null;

  return (
    <div className="chart" ref={ref}>
      <div className="legend" style={{ marginBottom: 8 }}>
        <span>
          <i className="swatch" style={{ background: "var(--accent)" }} />
          {emphasisLabel}
        </span>
        <span>
          <i className="swatch" style={{ background: "var(--muted-mark)" }} />
          {restLabel}
        </span>
      </div>
      <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={`${unitLabel}: распределение`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className={t === 0 ? "baseline" : "grid-line"} x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="axis-label" x={PAD.left - 6} y={y(t) + 4} textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        {items.map((item, i) => {
          const cx = PAD.left + slot * i + slot / 2;
          const h = (item.value / max) * plotH;
          return (
            <g key={item.label} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}>
              <rect x={cx - slot / 2} y={PAD.top} width={slot} height={plotH} fill="transparent" />
              {item.value > 0 && (
                <path
                  d={topRounded(cx - barW / 2, y(item.value), barW, h)}
                  fill={item.emphasis ? "var(--accent)" : "var(--muted-mark)"}
                  opacity={hover === null || hover === i ? 1 : 0.55}
                />
              )}
              <text className="axis-label" x={cx} y={H - PAD.bottom + 16} textAnchor="middle">
                {item.label}
              </text>
            </g>
          );
        })}
      </svg>
      {hovered && hover !== null && (
        <div className="tooltip" style={{ left: PAD.left + slot * hover + slot / 2, top: y(hovered.value) }}>
          <b>
            {unitLabel} {hovered.label}
          </b>
          <div>
            {formatNumber(hovered.value)} шт. · {formatPercent(total ? hovered.value / total : 0)} всех указаний
          </div>
          {hovered.tip && <div className="muted">{hovered.tip}</div>}
        </div>
      )}
      </div>
    </div>
  );
}

export function Meter({ value, tone }: { value: number; tone?: "warning" | "critical" }) {
  return (
    <div className={`meter ${tone ?? ""}`} role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={value}>
      <span style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}
