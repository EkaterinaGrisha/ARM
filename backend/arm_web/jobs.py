"""Job storage on disk and background processing.

Job folder layout::

    <data>/jobs/<id>/job.json           job state
    <data>/jobs/<id>/input/...          the uploaded doc set, folder structure kept
    <data>/jobs/<id>/pipeline_output/   the job process output: report, progress, log
    <data>/jobs/<id>/report.json        the report
    <data>/jobs/<id>/cache/             rendered pages
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import traceback
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from .models import Job, Report
from .pipeline import Pipeline

log = logging.getLogger(__name__)

_JOB_ID = re.compile(r"^[a-f0-9]{12}$")
_CHUNK = 1024 * 1024


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def safe_relative_path(name: str) -> Path:
    """Relative path from an uploaded file name that cannot leave the job folder."""
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".", "..")]
    if parts and re.fullmatch(r"[A-Za-z]:", parts[0]):  # a Windows drive
        parts = parts[1:]
    parts = [re.sub(r'[\x00-\x1f<>:"|?*]', "_", p).strip() or "_" for p in parts]
    return Path(*parts) if parts else Path("file")


class JobNotFound(KeyError):
    pass


class UploadTooLarge(ValueError):
    pass


def _copy(stream: BinaryIO, out: BinaryIO, limit: int | None) -> int:
    """Copy ``stream`` into ``out``; raise UploadTooLarge past ``limit`` bytes. Returns the bytes copied."""
    copied = 0
    while chunk := stream.read(_CHUNK):
        copied += len(chunk)
        if limit is not None and copied > limit:
            raise UploadTooLarge
        out.write(chunk)
    return copied


class JobStore:
    def __init__(self, data_dir: Path, pipeline_factory: Callable[[], Pipeline], workers: int = 1):
        self.root = data_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.pipeline_factory = pipeline_factory
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="arm-job")
        self._lock = threading.Lock()
        self._recover()

    # ------------------------------------------------------------------ storage
    def _dir(self, job_id: str) -> Path:
        if not _JOB_ID.match(job_id):
            raise JobNotFound(job_id)
        path = self.root / job_id
        if not (path / "job.json").exists():
            raise JobNotFound(job_id)
        return path

    def _save(self, job: Job) -> None:
        path = self.root / job.id / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def get(self, job_id: str) -> Job:
        return Job.model_validate_json((self._dir(job_id) / "job.json").read_text(encoding="utf-8"))

    def list(self) -> list[Job]:
        jobs = []
        for path in self.root.glob("*/job.json"):
            try:
                jobs.append(Job.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                log.warning("Пропущен повреждённый файл задания %s", path)
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def input_dir(self, job_id: str) -> Path:
        return self._dir(job_id) / "input"

    def report_path(self, job_id: str) -> Path:
        return self._dir(job_id) / "report.json"

    def cache_dir(self, job_id: str) -> Path:
        path = self._dir(job_id) / "cache"
        path.mkdir(exist_ok=True)
        return path

    def load_report(self, job_id: str) -> Report:
        return Report.model_validate_json(self.report_path(job_id).read_text(encoding="utf-8"))

    def document_path(self, job_id: str, doc_id: str) -> Path:
        report = self.load_report(job_id)
        for doc in report.documents:
            if doc.id == doc_id:
                base = self.input_dir(job_id).resolve()
                path = (base / doc.path).resolve()
                if base in path.parents and path.is_file():
                    return path
        raise JobNotFound(doc_id)

    # ------------------------------------------------------------------ lifecycle
    def create(self, name: str, files: Sequence[tuple[str, BinaryIO]], *, max_bytes: int | None = None) -> Job:
        """Store the upload and queue the job; past ``max_bytes`` in total nothing is kept."""
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.root / job_id
        input_dir = job_dir / "input"
        input_dir.mkdir(parents=True)
        total = 0
        used: set[Path] = set()
        try:
            for rel_name, stream in files:
                rel = safe_relative_path(rel_name)
                while rel in used:  # equal names: never overwrite
                    rel = rel.with_name(f"{rel.stem}_{len(used)}{rel.suffix}")
                used.add(rel)
                target = input_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as out:
                    total += _copy(stream, out, None if max_bytes is None else max_bytes - total)
        except UploadTooLarge:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        job = Job(
            id=job_id,
            name=name.strip() or f"Комплект от {datetime.now():%d.%m.%Y %H:%M}",
            created_at=now(),
            files_count=len(files),
            total_bytes=total,
            stage="В очереди",
        )
        self._save(job)
        self.executor.submit(self._run, job_id)
        return job

    def rerun(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status in ("queued", "running"):
            return job
        job.status, job.progress, job.stage, job.error, job.message = "queued", 0.0, "В очереди", None, None
        job.started_at = job.finished_at = None
        self._save(job)
        shutil.rmtree(self._dir(job_id) / "cache", ignore_errors=True)
        self.executor.submit(self._run, job_id)
        return job

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.status == "running":
            raise RuntimeError("Задание обрабатывается — удалите его после завершения")
        shutil.rmtree(self._dir(job_id))

    def _update(self, job_id: str, **changes) -> Job:
        with self._lock:
            job = self.get(job_id)
            for key, value in changes.items():
                setattr(job, key, value)
            self._save(job)
            return job

    def _run(self, job_id: str) -> None:
        try:
            job = self._update(job_id, status="running", started_at=now(), stage="Подготовка", progress=0.0)
            pipeline = self.pipeline_factory()

            def progress(stage: str, value: float, message: str | None = None) -> None:
                self._update(job_id, stage=stage, progress=round(max(0.0, min(1.0, value)), 3), message=message)

            report = pipeline.run(self.input_dir(job_id), kit_id=job_id, kit_name=job.name, progress=progress)
            report.kit.created_at = job.created_at
            path = self.report_path(job_id)
            path.with_suffix(".tmp").write_text(report.model_dump_json(exclude_none=True), encoding="utf-8")
            path.with_suffix(".tmp").replace(path)
            self._update(
                job_id,
                status="done",
                progress=1.0,
                stage="Готово",
                message=None,
                finished_at=now(),
                summary=report.summary,
                root_designation=report.kit.root_designation,
            )
        except Exception as exc:  # a job fails as a whole only when the pipeline itself fails
            log.error("Задание %s завершилось ошибкой:\n%s", job_id, traceback.format_exc())
            try:
                self._update(
                    job_id, status="failed", error=str(exc) or exc.__class__.__name__, finished_at=now(), stage="Ошибка"
                )
            except JobNotFound:
                pass

    def _recover(self) -> None:
        """After a server restart: interrupted jobs are marked failed, waiting ones are queued again."""
        for job in self.list():
            if job.status == "running":
                job.status, job.error, job.stage = "failed", "Обработка прервана перезапуском сервера", "Ошибка"
                self._save(job)
            elif job.status == "queued":
                self.executor.submit(self._run, job.id)
