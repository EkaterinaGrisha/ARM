import { ChevronLeft, ChevronRight, Download, Maximize2, X, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { documentFileUrl, pageImageUrl } from "../api/client";
import type { BBox } from "../api/types";
import { formatBytes, formatPercent } from "../lib/format";
import { DOCUMENT_KIND, METHOD, ORIGIN } from "../lib/labels";
import { sameBox, type SourceEntry } from "../lib/report";
import { useReport } from "../report/ReportContext";
import { DocStatusBadge } from "./common";

const ZOOMS = [1, 1.5, 2, 3, 4];

/** Масштаб, при котором подсвеченная область хорошо видна, но остаётся контекст вокруг неё. */
function autoZoom(box: BBox | null): number {
  if (!box) return 1;
  const w = box[2] - box[0];
  const target = Math.min(3, 0.3 / Math.max(w, 0.02));
  return ZOOMS.reduce((best, z) => (Math.abs(z - target) < Math.abs(best - target) ? z : best), 1);
}

/** Просмотр страницы документа с подсветкой областей, из которых извлечены значения. */
export function DocumentViewer() {
  const { viewer, index, jobId, closeViewer, openSource, openDocument } = useReport();
  const doc = viewer ? index.docs.get(viewer.docId) : undefined;
  const pageCount = doc?.pages.length ?? 0;
  const pageNo = Math.min(Math.max(1, viewer?.page ?? 1), Math.max(1, pageCount));
  const page = doc?.pages[pageNo - 1];
  const [zoom, setZoom] = useState(() => autoZoom(viewer?.box ?? null));
  const [imgState, setImgState] = useState<"loading" | "ok" | "error">("loading");
  const [hoverKey, setHoverKey] = useState<string | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLDivElement>(null);

  const entries = useMemo(
    () => (doc ? (index.sourcesByDoc.get(doc.id) ?? []).filter((e) => (e.source.page || 1) === pageNo) : []),
    [doc, index, pageNo],
  );
  const boxes = useMemo(() => {
    const seen: { box: BBox; entries: SourceEntry[] }[] = [];
    for (const e of entries) {
      const b = e.source.bbox;
      if (!b) continue;
      const found = seen.find((s) => sameBox(s.box, b));
      if (found) found.entries.push(e);
      else seen.push({ box: b, entries: [e] });
    }
    return seen;
  }, [entries]);
  const groups = useMemo(() => {
    const map = new Map<string, SourceEntry[]>();
    for (const e of entries) map.set(e.group, [...(map.get(e.group) ?? []), e]);
    return [...map.entries()];
  }, [entries]);

  const imageUrl = doc && page ? pageImageUrl(jobId, doc.id, pageNo) : null;
  const fileUrl = doc ? documentFileUrl(jobId, doc.id) : null;
  const activeBox = viewer?.box ?? null;

  const boxKey = activeBox?.join(",");
  useEffect(() => {
    setZoom(autoZoom(activeBox));
  }, [viewer?.docId, boxKey]); // рамка — массив, сравниваем по значению

  useEffect(() => setImgState(imageUrl ? "loading" : "error"), [imageUrl]);

  useEffect(() => {
    if (!viewer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeViewer();
      if (e.key === "ArrowRight" && doc && pageNo < pageCount) openDocument(doc.id, pageNo + 1);
      if (e.key === "ArrowLeft" && doc && pageNo > 1) openDocument(doc.id, pageNo - 1);
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [viewer, doc, pageNo, pageCount, closeViewer, openDocument]);

  // прокрутить к подсвеченной области
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const target = activeRef.current;
    if (!canvas || !target) {
      canvas?.scrollTo({ top: 0, left: 0 });
      return;
    }
    const c = canvas.getBoundingClientRect();
    const t = target.getBoundingClientRect();
    canvas.scrollTo({
      left: canvas.scrollLeft + t.left - c.left - c.width / 2 + t.width / 2,
      top: canvas.scrollTop + t.top - c.top - c.height / 2 + t.height / 2,
      behavior: "smooth",
    });
  }, [zoom, activeBox, pageNo, imgState]);

  if (!viewer || !doc) return null;
  const aspect = page && page.width > 0 && page.height > 0 ? page.height / page.width : 1.414;

  return (
    <div className="viewer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && closeViewer()}>
      <div className="viewer" role="dialog" aria-modal="true" aria-label={`Документ ${doc.file_name}`}>
        <div className="viewer-head">
          <div className="title">
            <b title={doc.path}>{doc.file_name}</b>
            <span>
              {DOCUMENT_KIND[doc.kind]} · {ORIGIN[doc.origin]}
              {doc.designation && <> · <span className="mono">{doc.designation}</span></>}
            </span>
          </div>
          {pageCount > 1 && (
            <div className="row" style={{ gap: 4 }}>
              <button className="btn sm icon ghost" disabled={pageNo <= 1} onClick={() => openDocument(doc.id, pageNo - 1)} aria-label="Предыдущая страница">
                <ChevronLeft size={16} />
              </button>
              <span className="num" style={{ fontSize: 13 }}>
                {pageNo} / {pageCount}
              </span>
              <button className="btn sm icon ghost" disabled={pageNo >= pageCount} onClick={() => openDocument(doc.id, pageNo + 1)} aria-label="Следующая страница">
                <ChevronRight size={16} />
              </button>
            </div>
          )}
          <div className="row" style={{ gap: 4 }}>
            <button className="btn sm icon ghost" onClick={() => setZoom((z) => ZOOMS[Math.max(0, ZOOMS.indexOf(z) - 1)] ?? 1)} aria-label="Уменьшить" disabled={zoom <= ZOOMS[0]}>
              <ZoomOut size={16} />
            </button>
            <span className="num" style={{ fontSize: 13, minWidth: 40, textAlign: "center" }}>
              {Math.round(zoom * 100)}%
            </span>
            <button className="btn sm icon ghost" onClick={() => setZoom((z) => ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + 1)] ?? z)} aria-label="Увеличить" disabled={zoom >= ZOOMS[ZOOMS.length - 1]}>
              <ZoomIn size={16} />
            </button>
            <button className="btn sm icon ghost" onClick={() => setZoom(1)} aria-label="По ширине" title="По ширине">
              <Maximize2 size={15} />
            </button>
          </div>
          {fileUrl && (
            <a className="btn sm" href={fileUrl} download title="Скачать исходный файл">
              <Download size={14} /> Файл
            </a>
          )}
          <button className="btn sm icon ghost" onClick={closeViewer} aria-label="Закрыть (Esc)">
            <X size={18} />
          </button>
        </div>

        <div className="viewer-canvas" ref={canvasRef}>
          {page ? (
            <div className="page-sheet" style={{ width: `${zoom * 100}%`, aspectRatio: `${1 / aspect}` }}>
              {imageUrl && imgState !== "error" && (
                <img src={imageUrl} alt={`Страница ${pageNo}`} onLoad={() => setImgState("ok")} onError={() => setImgState("error")} draggable={false} />
              )}
              {imgState === "error" && (
                <div className="no-image">
                  <div>
                    Изображение страницы недоступно
                    <br />
                    <small>{imageUrl ? "Не удалось загрузить страницу" : "Отчёт открыт из файла — показаны только области значений"}</small>
                  </div>
                </div>
              )}
              {boxes.map(({ box, entries: list }) => {
                const isActive = sameBox(box, activeBox);
                const isHover = list.some((e) => e.key === hoverKey);
                return (
                  <div
                    key={box.join(",")}
                    ref={isActive ? activeRef : undefined}
                    className={`box ${isActive ? "active" : ""} ${isHover ? "hover" : ""}`}
                    style={{
                      left: `${box[0] * 100}%`,
                      top: `${box[1] * 100}%`,
                      width: `${Math.max(0.002, box[2] - box[0]) * 100}%`,
                      height: `${Math.max(0.002, box[3] - box[1]) * 100}%`,
                    }}
                    title={list.map((e) => `${e.label}: ${e.value}`).join("\n")}
                    onClick={() => openSource(list[0].source)}
                  />
                );
              })}
            </div>
          ) : (
            <div className="empty">
              <h3>Страницы недоступны</h3>
              {doc.problem}
            </div>
          )}
        </div>

        <aside className="viewer-side">
          <div className="viewer-meta">
            <div className="row">
              <DocStatusBadge status={doc.status} />
              <span className="subtle">{formatBytes(doc.size_bytes)}</span>
            </div>
            {doc.problem && <div className="muted">{doc.problem}</div>}
            {doc.notes.map((n) => (
              <div key={n} className="muted">
                {n}
              </div>
            ))}
            {page && (page.format || page.problem) && (
              <div className="muted">
                Страница {pageNo}
                {page.format && <> · формат {page.format}</>}
                {page.problem && <> · {page.problem}</>}
              </div>
            )}
            <div className="subtle" style={{ overflowWrap: "anywhere" }}>
              {doc.path}
            </div>
          </div>
          {groups.length === 0 && <p className="muted" style={{ padding: 14 }}>На этой странице нет привязанных значений.</p>}
          {groups.map(([group, list]) => (
            <div key={group}>
              <h4>{group}</h4>
              {list.map((e) => {
                const active = sameBox(e.source.bbox, activeBox);
                return (
                  <button
                    key={e.key}
                    type="button"
                    className={`src-item ${active ? "active" : ""}`}
                    onMouseEnter={() => setHoverKey(e.key)}
                    onMouseLeave={() => setHoverKey(null)}
                    onClick={() => openSource(e.source)}
                    disabled={!e.source.bbox}
                  >
                    <div className="l">
                      {e.label}
                      {e.source.method && <> · {METHOD[e.source.method]}</>}
                      {e.source.confidence !== null && e.source.confidence !== undefined && <> · {formatPercent(e.source.confidence)}</>}
                    </div>
                    <div className="v">{e.value || <span className="subtle">—</span>}</div>
                  </button>
                );
              })}
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}
