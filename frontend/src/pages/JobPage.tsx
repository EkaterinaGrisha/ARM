import { ArrowLeft, CircleX, LoaderCircle, RotateCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, DEMO_ID, LOCAL_ID } from "../api/client";
import type { Job, Report } from "../api/types";
import { Empty } from "../components/common";
import { formatDate, formatPercent } from "../lib/format";
import { JOB_STATUS } from "../lib/labels";
import { ReportView } from "../report/ReportView";

function ProcessingView({ job }: { job: Job }) {
  const started = job.started_at ? new Date(job.started_at).getTime() : null;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = started ? Math.max(0, Math.round((now - started) / 1000)) : null;
  return (
    <div className="card card-body stack" style={{ maxWidth: 720, margin: "40px auto" }}>
      <div className="row">
        <LoaderCircle size={20} className="spin" />
        <h2>{job.status === "queued" ? "Комплект в очереди" : "Идёт обработка комплекта"}</h2>
      </div>
      <div>
        <div className="row" style={{ marginBottom: 6 }}>
          <b>{job.stage ?? JOB_STATUS[job.status]}</b>
          <span className="spacer" />
          <span className="num muted">{formatPercent(job.progress)}</span>
        </div>
        <div className={`progress ${job.status === "queued" ? "indeterminate" : ""}`}>
          <span style={{ width: `${job.progress * 100}%` }} />
        </div>
        <p className="muted" style={{ marginTop: 8, minHeight: 21, overflowWrap: "anywhere" }}>
          {job.message ?? " "}
        </p>
      </div>
      <dl className="kv">
        <dt>Комплект</dt>
        <dd>{job.name}</dd>
        <dt>Файлов</dt>
        <dd>{job.files_count}</dd>
        <dt>Загружен</dt>
        <dd>{formatDate(job.created_at)}</dd>
        {elapsed !== null && (
          <>
            <dt>Прошло</dt>
            <dd className="num">{elapsed} с</dd>
          </>
        )}
      </dl>
      <p className="subtle" style={{ fontSize: 13 }}>
        Страницу можно закрыть — обработка продолжится на сервере, отчёт появится в списке проверок.
      </p>
    </div>
  );
}

export function JobPage() {
  const { jobId = DEMO_ID } = useParams();
  const isStatic = jobId === DEMO_ID || jobId === LOCAL_ID;
  const [job, setJob] = useState<Job | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const loadReport = useCallback(() => {
    api
      .report(jobId)
      .then(setReport)
      .catch((e) => setError(e.message));
  }, [jobId]);

  useEffect(() => {
    setJob(null);
    setReport(null);
    setError(null);
    if (isStatic) {
      loadReport();
      return;
    }
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = () =>
      api
        .job(jobId)
        .then((j) => {
          if (!alive) return;
          setJob(j);
          if (j.status === "done") loadReport();
          else if (j.status === "queued" || j.status === "running") timer = setTimeout(poll, 1000);
        })
        .catch((e) => alive && setError(e.message));
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [jobId, isStatic, loadReport, attempt]);

  const rerun = async () => {
    try {
      await api.rerun(jobId);
      setAttempt((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error) {
    return (
      <div className="card">
        <Empty icon={<CircleX size={28} />} title="Не удалось открыть отчёт">
          <p style={{ marginBottom: 12 }}>{error}</p>
          <Link className="btn" to="/">
            <ArrowLeft size={16} /> К списку проверок
          </Link>
        </Empty>
      </div>
    );
  }
  if (job && (job.status === "queued" || job.status === "running")) return <ProcessingView job={job} />;
  if (job && job.status === "failed") {
    return (
      <div className="card" style={{ maxWidth: 720, margin: "40px auto" }}>
        <Empty icon={<CircleX size={28} />} title={`Обработка «${job.name}» завершилась ошибкой`}>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", textAlign: "left", margin: "12px 0", fontSize: 12.5 }}>
            {job.error}
          </pre>
          <div className="row" style={{ justifyContent: "center" }}>
            <Link className="btn" to="/">
              <ArrowLeft size={16} /> К списку
            </Link>
            <button className="btn primary" onClick={rerun}>
              <RotateCw size={16} /> Обработать заново
            </button>
          </div>
        </Empty>
      </div>
    );
  }
  if (!report) return <Empty icon={<LoaderCircle size={24} className="spin" />} title="Загрузка отчёта…" />;
  return <ReportView jobId={jobId} report={report} />;
}
