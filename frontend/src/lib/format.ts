import type { Extracted, Parameter } from "../api/types";

const num = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const pct = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 0 });

export function formatNumber(v: number | null | undefined): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : num.format(v);
}

export function formatPercent(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : pct.format(v);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  const units = ["КБ", "МБ", "ГБ"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${num.format(Math.round(v * 10) / 10)} ${units[i]}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  if (sec < 60) return `${num.format(Math.round(sec * 10) / 10)} с`;
  const m = Math.floor(sec / 60);
  return `${m} мин ${Math.round(sec % 60)} с`;
}

export function extractedText(ex: Extracted | null | undefined): string {
  if (!ex || ex.value === null || ex.value === undefined || ex.value === "") return "";
  return typeof ex.value === "number" ? formatNumber(ex.value) : String(ex.value);
}

/** Значение параметра в человекочитаемом виде: «58…62 HRC», «0,8 мкм», «IT6». */
export function parameterValue(p: Parameter): string {
  const unit = p.unit ? ` ${p.unit}` : "";
  if (p.value_min !== null && p.value_min !== undefined && p.value_max !== null && p.value_max !== undefined) {
    return `${formatNumber(p.value_min)}…${formatNumber(p.value_max)}${unit}`;
  }
  if (p.value !== null && p.value !== undefined) return `${formatNumber(p.value)}${unit}`;
  return "—";
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
