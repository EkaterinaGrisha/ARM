import { createContext, type ReactNode, useCallback, useContext, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { BBox, Report, SourceRef } from "../api/types";
import { buildIndex, type ReportIndex } from "../lib/report";

export interface ViewerTarget {
  docId: string;
  page: number;
  box: BBox | null;
}

interface ReportContextValue {
  jobId: string;
  report: Report;
  index: ReportIndex;
  viewer: ViewerTarget | null;
  openSource: (source: SourceRef) => void;
  openDocument: (docId: string, page?: number) => void;
  closeViewer: () => void;
}

const Ctx = createContext<ReportContextValue | null>(null);

function parseBox(raw: string | null): BBox | null {
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  return parts.length === 4 && parts.every((v) => Number.isFinite(v)) ? (parts as BBox) : null;
}

/** Состояние просмотрщика хранится в адресной строке: ссылку на значение можно переслать коллеге. */
export function ReportProvider({ jobId, report, children }: { jobId: string; report: Report; children: ReactNode }) {
  const [params, setParams] = useSearchParams();
  const index = useMemo(() => buildIndex(report), [report]);

  const docId = params.get("doc");
  const viewer = useMemo<ViewerTarget | null>(
    () => (docId && index.docs.has(docId) ? { docId, page: Number(params.get("page")) || 1, box: parseBox(params.get("box")) } : null),
    [docId, params, index],
  );

  const open = useCallback(
    (doc: string, page: number, box: BBox | null | undefined) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("doc", doc);
        next.set("page", String(page));
        if (box) next.set("box", box.map((v) => v.toFixed(4)).join(","));
        else next.delete("box");
        return next;
      });
    },
    [setParams],
  );

  const value = useMemo<ReportContextValue>(
    () => ({
      jobId,
      report,
      index,
      viewer,
      openSource: (s) => open(s.document_id, s.page || 1, s.bbox ?? null),
      openDocument: (doc, page = 1) => open(doc, page, null),
      closeViewer: () =>
        setParams((prev) => {
          const next = new URLSearchParams(prev);
          ["doc", "page", "box"].forEach((k) => next.delete(k));
          return next;
        }),
    }),
    [jobId, report, index, viewer, open, setParams],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useReport(): ReportContextValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useReport вне ReportProvider");
  return ctx;
}
