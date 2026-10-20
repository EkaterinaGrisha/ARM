import type { Health, Job, Report } from "./types";

/** Особые идентификаторы отчётов, которые открываются без сервера. */
export const DEMO_ID = "demo";
export const LOCAL_ID = "local";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError("Сервер недоступен", 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* тело без JSON */
    }
    throw new ApiError(detail || `Ошибка ${res.status}`, res.status);
  }
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  if (!type.includes("json")) throw new ApiError("Сервер вернул неожиданный ответ", res.status);
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  rerun: (id: string) => request<Job>(`/api/jobs/${id}/rerun`, { method: "POST" }),
  remove: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  report: (id: string) => {
    if (id === DEMO_ID) return request<Report>("/demo/report.json");
    if (id === LOCAL_ID) {
      const local = loadLocalReport();
      return local ? Promise.resolve(local) : Promise.reject(new ApiError("Отчёт из файла не найден — откройте его снова", 404));
    }
    return request<Report>(`/api/jobs/${id}/report`);
  },

  /** Загрузка комплекта с прогрессом (fetch не умеет отдавать прогресс отправки). */
  createJob(name: string, files: { file: File; path: string }[], onProgress: (share: number) => void): Promise<Job> {
    const form = new FormData();
    form.append("name", name);
    for (const { file, path } of files) {
      form.append("files", file, file.name);
      form.append("paths", path);
    }
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/jobs");
      xhr.responseType = "json";
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as Job);
        else reject(new ApiError(xhr.response?.detail ?? `Ошибка загрузки (${xhr.status})`, xhr.status));
      };
      xhr.onerror = () => reject(new ApiError("Сервер недоступен", 0));
      xhr.send(form);
    });
  },
};

export function pageImageUrl(jobId: string, docId: string, page: number): string | null {
  if (jobId === LOCAL_ID) return null;
  if (jobId === DEMO_ID) return `/demo/pages/${docId}-${page}.png`;
  return `/api/jobs/${jobId}/documents/${docId}/pages/${page}.png`;
}

export function documentFileUrl(jobId: string, docId: string): string | null {
  if (jobId === LOCAL_ID || jobId === DEMO_ID) return null;
  return `/api/jobs/${jobId}/documents/${docId}/file`;
}

export type ExportFormat = "json" | "xlsx" | "html";

export function exportUrl(jobId: string, format: ExportFormat): string | null {
  if (jobId === LOCAL_ID) return null;
  if (jobId === DEMO_ID) return `/demo/report.${format}`;
  return `/api/jobs/${jobId}/report.${format}`;
}

// ---------------------------------------------------------------- отчёт, открытый из файла

const LOCAL_KEY = "arm.localReport";
let localReport: Report | null = null;

export function saveLocalReport(report: Report): void {
  localReport = report;
  try {
    sessionStorage.setItem(LOCAL_KEY, JSON.stringify(report));
  } catch {
    /* большой отчёт или запрет хранилища — держим только в памяти */
  }
}

export function loadLocalReport(): Report | null {
  if (localReport) return localReport;
  try {
    const raw = sessionStorage.getItem(LOCAL_KEY);
    localReport = raw ? (JSON.parse(raw) as Report) : null;
  } catch {
    localReport = null;
  }
  return localReport;
}

export function parseReport(text: string): Report {
  const data = JSON.parse(text);
  if (!data || typeof data !== "object" || !data.kit || !Array.isArray(data.documents)) {
    throw new Error("Файл не похож на отчёт: нет полей kit и documents");
  }
  const empty = { specifications: [], tree: [], discrepancies: [], parts: [], critical_parameters: [], log: [] };
  return { ...empty, ...data, summary: { documents_by_status: {}, documents_by_kind: {}, discrepancies_by_type: {}, ...data.summary } };
}
