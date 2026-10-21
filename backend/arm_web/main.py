"""HTTP API of the web UI and serving of the built frontend.

    uvicorn arm_web.main:app --port 8000

Settings (environment variables):
    ARM_DATA_DIR          job folder (default ./data)
    ARM_PIPELINE_CMD      external team pipeline (see arm_web/pipeline/external.py)
    ARM_PIPELINE_TIMEOUT  seconds per doc set, default 3600
    ARM_JOB_MEMORY_MB     memory limit of the job process, default 8192, 0 for none (enforced on Linux)
    ARM_INPROCESS         1 runs the core in API threads without limits (rollback)
    ARM_MAX_UPLOAD_MB     upload size limit, default 2048
    ARM_MAX_FILES         files per upload, default 2000
    ARM_MAX_PAGES         pages read per file, default 500
    ARM_OCR               0 turns scan recognition off
    ARM_WORKERS           doc sets processed at once (default 1)
    ARM_FRONTEND_DIST     built frontend (default ../frontend/dist)
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from kdcheck.render import RenderError, render_page
from kdcheck.report import to_html, to_xlsx
from kdcheck.run import ocr_available
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from . import __version__
from .jobs import JobNotFound, JobStore, UploadTooLarge
from .models import Job
from .pipeline import get_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DATA_DIR = Path(os.environ.get("ARM_DATA_DIR", "data"))
FRONTEND_DIST = Path(os.environ.get("ARM_FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))

app = FastAPI(title="АРМ — входной контроль КД", version=__version__)
store = JobStore(DATA_DIR, get_pipeline, workers=int(os.environ.get("ARM_WORKERS", "1")))


def _job_or_404(job_id: str) -> Job:
    try:
        return store.get(job_id)
    except JobNotFound:
        raise HTTPException(404, "Задание не найдено") from None


def _report_path_or_404(job_id: str) -> Path:
    job = _job_or_404(job_id)
    path = store.report_path(job_id)
    if job.status != "done" or not path.exists():
        raise HTTPException(409, "Отчёт ещё не готов")
    return path


def _attachment(name: str) -> dict[str, str]:
    ascii_name = name.encode("ascii", "ignore").decode() or "report"
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"}


def _limit(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@app.get("/api/health")
def health() -> dict:
    pipeline = get_pipeline()
    return {
        "status": "ok",
        "version": __version__,
        "pipeline": pipeline.name,
        "pipeline_version": pipeline.version,
        "ocr": ocr_available(),
        "max_upload_mb": _limit("ARM_MAX_UPLOAD_MB", 2048),
        "max_files": _limit("ARM_MAX_FILES", 2000),
    }


@app.get("/api/jobs")
def list_jobs() -> list[Job]:
    return store.list()


UPLOAD_FORM = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["files"],
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Файлы комплекта",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Относительные пути файлов (в том же порядке)",
                        },
                        "name": {"type": "string"},
                    },
                }
            }
        },
    }
}


@app.post("/api/jobs", status_code=201, openapi_extra=UPLOAD_FORM)
async def create_job(request: Request) -> Job:
    max_mb, max_files = _limit("ARM_MAX_UPLOAD_MB", 2048), _limit("ARM_MAX_FILES", 2000)
    too_large = f"Комплект больше {max_mb} МБ: разделите его или увеличьте ARM_MAX_UPLOAD_MB"
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > max_mb * 1024 * 1024:
        raise HTTPException(413, too_large)
    # The size limit bounds the upload, so the parser may take any number of files; their count is checked below.
    async with request.form(max_files=math.inf, max_fields=math.inf) as form:
        files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
        if not files:
            raise HTTPException(422, "Не выбрано ни одного файла")
        if len(files) > max_files:
            raise HTTPException(
                413, f"Файлов в комплекте: {len(files)}, допустимо не больше {max_files}. Увеличьте ARM_MAX_FILES"
            )
        paths = [p if isinstance(p, str) else "" for p in form.getlist("paths")]
        name = form.get("name")
        items = []
        for i, f in enumerate(files):
            rel = paths[i] if i < len(paths) and paths[i] else (f.filename or f"file_{i}")
            items.append((rel, f.file))
        try:
            return await run_in_threadpool(
                store.create, name if isinstance(name, str) else "", items, max_bytes=max_mb * 1024 * 1024
            )
        except UploadTooLarge:
            raise HTTPException(413, too_large) from None


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Job:
    return _job_or_404(job_id)


@app.post("/api/jobs/{job_id}/rerun")
def rerun_job(job_id: str) -> Job:
    _job_or_404(job_id)
    return store.rerun(job_id)


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> Response:
    _job_or_404(job_id)
    try:
        store.delete(job_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return Response(status_code=204)


@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str) -> FileResponse:
    return FileResponse(_report_path_or_404(job_id), media_type="application/json")


@app.get("/api/jobs/{job_id}/report.json")
def download_report_json(job_id: str) -> FileResponse:
    job = _job_or_404(job_id)
    return FileResponse(
        _report_path_or_404(job_id), media_type="application/json", headers=_attachment(f"Отчёт {job.name}.json")
    )


@app.get("/api/jobs/{job_id}/report.xlsx")
def download_report_xlsx(job_id: str) -> Response:
    job = _job_or_404(job_id)
    _report_path_or_404(job_id)
    data = to_xlsx(store.load_report(job_id))
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_attachment(f"Отчёт {job.name}.xlsx"),
    )


@app.get("/api/jobs/{job_id}/report.html")
def download_report_html(job_id: str, download: bool = False) -> HTMLResponse:
    job = _job_or_404(job_id)
    _report_path_or_404(job_id)
    headers = _attachment(f"Отчёт {job.name}.html") if download else None
    return HTMLResponse(to_html(store.load_report(job_id)), headers=headers)


@app.get("/api/jobs/{job_id}/documents/{doc_id}/file")
def document_file(job_id: str, doc_id: str) -> FileResponse:
    _report_path_or_404(job_id)
    try:
        path = store.document_path(job_id, doc_id)
    except JobNotFound:
        raise HTTPException(404, "Документ не найден") from None
    return FileResponse(path, filename=path.name)


@app.get("/api/jobs/{job_id}/documents/{doc_id}/pages/{page}.png")
def document_page(job_id: str, doc_id: str, page: int) -> Response:
    _report_path_or_404(job_id)
    try:
        path = store.document_path(job_id, doc_id)
    except JobNotFound:
        raise HTTPException(404, "Документ не найден") from None
    cached = store.cache_dir(job_id) / f"{doc_id}-{page}.png"
    if not cached.exists():
        try:
            cached.write_bytes(render_page(path, page))
        except RenderError as exc:
            raise HTTPException(422, str(exc)) from None
    return FileResponse(cached, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- фронтенд (SPA)

if FRONTEND_DIST.is_dir():
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (FRONTEND_DIST / path).resolve()
        if path and FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
