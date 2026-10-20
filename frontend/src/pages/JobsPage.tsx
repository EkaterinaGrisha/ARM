import { FileJson, FlaskConical, FolderOpen, LoaderCircle, Plus, RotateCw, ServerOff, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, LOCAL_ID, parseReport, saveLocalReport } from "../api/client";
import type { Job } from "../api/types";
import { Badge, Empty } from "../components/common";
import { DataTable } from "../components/DataTable";
import { useServer } from "../components/Layout";
import { formatBytes, formatDate, formatPercent } from "../lib/format";
import { JOB_STATUS } from "../lib/labels";

function JobStatusCell({ job }: { job: Job }) {
  if (job.status === "running" || job.status === "queued") {
    return (
      <div style={{ minWidth: 160 }}>
        <div className="row" style={{ gap: 6, marginBottom: 4 }}>
          <LoaderCircle size={14} className="spin" />
          <span>{job.stage ?? JOB_STATUS[job.status]}</span>
          <span className="subtle num">{formatPercent(job.progress)}</span>
        </div>
        <div className="progress">
          <span style={{ width: `${job.progress * 100}%` }} />
        </div>
      </div>
    );
  }
  if (job.status === "failed") return <Badge tone="critical" title={job.error ?? undefined}>Ошибка</Badge>;
  return <Badge tone="good">Готово</Badge>;
}

export function JobsPage() {
  const navigate = useNavigate();
  const { offline, checked } = useServer();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .jobs()
      .then((j) => {
        setJobs(j);
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const active = jobs?.some((j) => j.status === "running" || j.status === "queued");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [active, load]);

  const openLocal = async (file: File) => {
    try {
      saveLocalReport(parseReport(await file.text()));
      navigate(`/jobs/${LOCAL_ID}`);
    } catch (e) {
      alert(`Не удалось открыть отчёт: ${(e as Error).message}`);
    }
  };

  const remove = async (job: Job) => {
    if (!confirm(`Удалить проверку «${job.name}» вместе с загруженными файлами?`)) return;
    try {
      await api.remove(job.id);
      load();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const rerun = async (job: Job) => {
    try {
      await api.rerun(job.id);
      load();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <div className="stack">
      <div className="page-head" style={{ marginBottom: 4 }}>
        <div>
          <h1>Проверки комплектов</h1>
          <p className="lead">
            Загрузите папку с комплектом конструкторской документации (PDF, JPG, PNG, TIFF) — сервис восстановит дерево изделия по
            спецификациям, найдёт расхождения и извлечёт параметры из основных надписей и технических требований.
          </p>
        </div>
        <div className="row">
          <input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={(e) => e.target.files?.[0] && openLocal(e.target.files[0])} />
          <button className="btn" onClick={() => fileInput.current?.click()} title="Открыть report.json, полученный командой python -m arm_web process">
            <FileJson size={16} /> Открыть report.json
          </button>
          <Link className="btn primary" to="/new">
            <Plus size={16} /> Новая проверка
          </Link>
        </div>
      </div>

      {checked && offline && (
        <div className="callout warning">
          <ServerOff size={18} />
          <div>
            <b>Сервер обработки недоступен.</b> Можно открыть демо-отчёт или отчёт из файла. Запуск сервера:{" "}
            <span className="mono">python -m arm_web serve</span> (см. README).
          </div>
        </div>
      )}

      <div className="card">
        <div className="item-row" style={{ alignItems: "center" }}>
          <span className="brand-mark" style={{ width: 36, height: 36, borderRadius: 9 }}>
            <FlaskConical size={18} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b>Демо-комплект «Редуктор колёсный»</b>
            <div className="muted" style={{ fontSize: 13 }}>
              22 файла: спецификации, чертежи, сканы и проблемные файлы с заложенными расхождениями. Отчёт открывается без сервера.
            </div>
          </div>
          <Link className="btn" to="/jobs/demo">
            Открыть демо-отчёт
          </Link>
        </div>
      </div>

      <div className="card">
        <div className="card-head" style={{ paddingBottom: 12 }}>
          <h2>История проверок</h2>
          <span className="spacer" />
          {jobs && <span className="muted">{jobs.length}</span>}
        </div>
        {error && !offline && (
          <div className="callout critical" style={{ margin: "0 16px 16px" }}>
            {error}
          </div>
        )}
        {jobs === null && !error && <Empty icon={<LoaderCircle size={24} className="spin" />} title="Загрузка…" />}
        {offline && <Empty icon={<ServerOff size={28} />} title="История недоступна без сервера" />}
        {jobs && jobs.length === 0 && (
          <Empty icon={<FolderOpen size={28} />} title="Проверок пока нет">
            <Link to="/new">Загрузите первый комплект</Link> или откройте демо-отчёт.
          </Empty>
        )}
        {jobs && jobs.length > 0 && (
          <DataTable
            rows={jobs}
            rowKey={(j) => j.id}
            onRowClick={(j) => navigate(`/jobs/${j.id}`)}
            columns={[
              { key: "name", header: "Комплект", sort: (j) => j.name, render: (j) => <b>{j.name}</b> },
              { key: "root", header: "Изделие", sort: (j) => j.root_designation, render: (j) => <span className="mono">{j.root_designation ?? "—"}</span> },
              {
                key: "files",
                header: "Файлы",
                className: "num",
                sort: (j) => j.files_count,
                render: (j) => (
                  <span className="nowrap">
                    {j.files_count} <span className="subtle">· {formatBytes(j.total_bytes)}</span>
                  </span>
                ),
              },
              { key: "status", header: "Статус", sort: (j) => j.status, render: (j) => <JobStatusCell job={j} /> },
              {
                key: "disc",
                header: "Расхождения",
                className: "num",
                sort: (j) => j.summary?.discrepancies_total ?? -1,
                render: (j) =>
                  j.summary ? (
                    j.summary.discrepancies_total ? <Badge tone="critical">{j.summary.discrepancies_total}</Badge> : <Badge tone="good">0</Badge>
                  ) : (
                    "—"
                  ),
              },
              {
                key: "problems",
                header: "Проблемные файлы",
                className: "num",
                render: (j) => {
                  if (!j.summary) return "—";
                  const ok = j.summary.documents_by_status.ok ?? 0;
                  const bad = j.summary.documents_total - ok;
                  return bad ? <Badge tone="warning">{bad}</Badge> : <span className="subtle">0</span>;
                },
              },
              { key: "created", header: "Создана", sort: (j) => j.created_at, render: (j) => <span className="nowrap muted">{formatDate(j.created_at)}</span> },
              {
                key: "actions",
                header: <span className="sr-only">Действия</span>,
                render: (j) => (
                  <div className="row" style={{ gap: 2, flexWrap: "nowrap" }} onClick={(e) => e.stopPropagation()}>
                    <button className="btn sm icon ghost" title="Обработать заново" aria-label="Обработать заново" disabled={j.status === "running" || j.status === "queued"} onClick={() => rerun(j)}>
                      <RotateCw size={15} />
                    </button>
                    <button className="btn sm icon ghost danger" title="Удалить" aria-label="Удалить" disabled={j.status === "running"} onClick={() => remove(j)}>
                      <Trash2 size={15} />
                    </button>
                  </div>
                ),
              },
            ]}
          />
        )}
      </div>
    </div>
  );
}
