import { FileText, FolderOpen, Image, LoaderCircle, Play, ServerOff, TriangleAlert, Upload, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/common";
import { useServer } from "../components/Layout";
import { formatBytes, formatPercent, plural } from "../lib/format";

interface PickedFile {
  file: File;
  path: string;
}

const PDF = /\.pdf$/i;
const IMAGE = /\.(jpe?g|png|tiff?|bmp)$/i;

function fileType(path: string): "pdf" | "image" | "other" {
  if (PDF.test(path)) return "pdf";
  if (IMAGE.test(path)) return "image";
  return "other";
}

// --- обход перетащенных папок (File and Directory Entries API)
async function readEntry(entry: FileSystemEntry, out: PickedFile[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
    out.push({ file, path: entry.fullPath.replace(/^\/+/, "") });
    return;
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
      if (!batch.length) break;
      for (const child of batch) await readEntry(child, out);
    }
  }
}

async function filesFromDrop(dt: DataTransfer): Promise<PickedFile[]> {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean) as FileSystemEntry[];
  if (!entries.length) return [...dt.files].map((file) => ({ file, path: file.name }));
  const out: PickedFile[] = [];
  for (const e of entries) await readEntry(e, out);
  return out;
}

function commonFolder(files: PickedFile[]): string | null {
  const first = files[0]?.path.split("/");
  if (!first || first.length < 2) return null;
  return files.every((f) => f.path.split("/")[0] === first[0]) ? first[0] : null;
}

export function NewJobPage() {
  const navigate = useNavigate();
  const { offline } = useServer();
  const [name, setName] = useState("");
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [over, setOver] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [upload, setUpload] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const add = (picked: PickedFile[]) => {
    const ignored = /(^|\/)(\.DS_Store|desktop\.ini)$/i;
    const clean = picked.filter((p) => !ignored.test(p.path));
    setFiles((prev) => {
      const seen = new Set(prev.map((p) => p.path));
      return [...prev, ...clean.filter((p) => !seen.has(p.path))];
    });
    if (!name) {
      const folder = commonFolder(clean);
      if (folder) setName(folder);
    }
    setError(null);
  };

  const stats = useMemo(() => {
    const s = { pdf: 0, image: 0, other: 0, bytes: 0 };
    for (const f of files) {
      s[fileType(f.path)]++;
      s.bytes += f.file.size;
    }
    return s;
  }, [files]);

  const submit = async () => {
    if (!files.length) return;
    setUpload(0);
    setError(null);
    try {
      const job = await api.createJob(name, files, setUpload);
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      setError((e as Error).message);
      setUpload(null);
    }
  };

  const visible = showAll ? files : files.slice(0, 12);

  return (
    <div className="stack">
      <div className="page-head" style={{ marginBottom: 4 }}>
        <div>
          <h1>Новая проверка</h1>
          <p className="lead">
            Выберите папку с комплектом целиком — структура подпапок сохранится. Обработка идёт в пакетном режиме, без участия
            пользователя; повреждённые и посторонние файлы не прерывают проверку, а попадают в отчёт отдельной категорией.
          </p>
        </div>
      </div>

      {offline && (
        <div className="callout warning">
          <ServerOff size={18} />
          <div>Сервер обработки недоступен — загрузка невозможна. Запустите <span className="mono">python -m arm_web serve</span>.</div>
        </div>
      )}

      <div className="split" style={{ gridTemplateColumns: "minmax(0, 1fr) 320px" }}>
        <div className="stack">
          <div className="card card-body stack">
            <div className="field">
              <label htmlFor="kit-name">Название комплекта</label>
              <input id="kit-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Например: Редуктор колёсный, ред. 06.2025" />
            </div>

            <div
              className={`dropzone ${over ? "over" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={async (e) => {
                e.preventDefault();
                setOver(false);
                add(await filesFromDrop(e.dataTransfer));
              }}
            >
              <div className="big-icon">
                <Upload size={22} />
              </div>
              <h3>Перетащите сюда папку или файлы комплекта</h3>
              <p className="muted" style={{ margin: "4px 0 14px" }}>
                PDF (векторные и сканы), JPG, PNG, TIFF. Прочие файлы тоже можно загрузить — они будут отмечены в отчёте.
              </p>
              <div className="row" style={{ justifyContent: "center" }}>
                <button type="button" className="btn" onClick={() => folderInput.current?.click()}>
                  <FolderOpen size={16} /> Выбрать папку
                </button>
                <button type="button" className="btn" onClick={() => filesInput.current?.click()}>
                  <FileText size={16} /> Выбрать файлы
                </button>
              </div>
              <input
                ref={folderInput}
                type="file"
                hidden
                multiple
                {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
                onChange={(e) => {
                  add([...(e.target.files ?? [])].map((file) => ({ file, path: file.webkitRelativePath || file.name })));
                  e.target.value = "";
                }}
              />
              <input
                ref={filesInput}
                type="file"
                hidden
                multiple
                onChange={(e) => {
                  add([...(e.target.files ?? [])].map((file) => ({ file, path: file.name })));
                  e.target.value = "";
                }}
              />
            </div>
          </div>

          {files.length > 0 && (
            <div className="card">
              <div className="toolbar">
                <b>
                  {files.length} {plural(files.length, "файл", "файла", "файлов")}
                </b>
                <span className="muted">· {formatBytes(stats.bytes)}</span>
                <span className="spacer" />
                <Badge tone="accent">
                  <FileText size={13} /> PDF: {stats.pdf}
                </Badge>
                <Badge tone="accent">
                  <Image size={13} /> Изображения: {stats.image}
                </Badge>
                {stats.other > 0 && (
                  <Badge tone="warning" title="Будут отмечены в отчёте как неподдерживаемые">
                    <TriangleAlert size={13} /> Прочие: {stats.other}
                  </Badge>
                )}
                <button className="btn sm ghost" onClick={() => setFiles([])} disabled={upload !== null}>
                  Очистить
                </button>
              </div>
              <ul className="list-plain">
                {visible.map((f) => (
                  <li key={f.path} className="item-row" style={{ padding: "6px 16px", alignItems: "center" }}>
                    {fileType(f.path) === "image" ? <Image size={15} className="kind-icon" /> : <FileText size={15} className="kind-icon" />}
                    <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>{f.path}</span>
                    {fileType(f.path) === "other" && <Badge tone="warning">не PDF/изображение</Badge>}
                    <span className="subtle nowrap num">{formatBytes(f.file.size)}</span>
                    <button
                      className="btn sm icon ghost"
                      aria-label={`Убрать ${f.path}`}
                      disabled={upload !== null}
                      onClick={() => setFiles((prev) => prev.filter((p) => p.path !== f.path))}
                    >
                      <X size={14} />
                    </button>
                  </li>
                ))}
              </ul>
              {files.length > 12 && (
                <div className="card-foot">
                  <button className="btn sm ghost" onClick={() => setShowAll((v) => !v)}>
                    {showAll ? "Свернуть список" : `Показать все ${files.length}`}
                  </button>
                </div>
              )}
            </div>
          )}

          {error && <div className="callout critical">{error}</div>}

          <div className="row">
            <button className="btn primary" onClick={submit} disabled={!files.length || upload !== null || offline} style={{ height: 40, padding: "0 20px" }}>
              {upload !== null ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}
              {upload !== null ? `Загрузка ${formatPercent(upload)}` : "Запустить проверку"}
            </button>
            {upload !== null && (
              <div className="progress" style={{ flex: 1, maxWidth: 360 }}>
                <span style={{ width: `${upload * 100}%` }} />
              </div>
            )}
          </div>
        </div>

        <aside className="card card-body stack" style={{ fontSize: 13.5 }}>
          <div>
            <h3 style={{ marginBottom: 6 }}>Блок 1. Комплектность</h3>
            <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
              <li>дерево изделия по спецификациям, СЕ раскрываются рекурсивно;</li>
              <li>сопоставление позиций с файлами чертежей по обозначению;</li>
              <li>отсутствующие и лишние чертежи, расхождения наименований, дубли обозначений, литеры и номера изменений.</li>
            </ul>
          </div>
          <div>
            <h3 style={{ marginBottom: 6 }}>Блок 2. Анализ документов</h3>
            <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
              <li>графы спецификаций и основных надписей;</li>
              <li>квалитеты, шероховатость, допуски формы, термообработка, покрытия;</li>
              <li>выделение параметров, критичных для производства.</li>
            </ul>
          </div>
          <div className="callout info" style={{ fontSize: 13 }}>
            Каждое значение в отчёте можно открыть в исходном документе с подсветкой области страницы.
          </div>
        </aside>
      </div>
    </div>
  );
}
