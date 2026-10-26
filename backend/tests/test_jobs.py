"""Jobs run in a separate process: a hung or memory-hungry job ends alone, the next one still runs (ADR-0006)."""

import json
import os
import sys
import time
from pathlib import Path

import pytest
from arm_web.jobs import JobStore, UploadTooLarge
from arm_web.pipeline import get_pipeline
from arm_web.pipeline.process import ProcessPipeline
from arm_web.settings import config_dir
from kdcheck.run import BaselinePipeline

# Writes a minimal valid report.json into the output folder.
REPORT = [sys.executable, "-c", "import json, sys; json.dump({'kit': {'id': 'x', 'name': ''}}, open(sys.argv[1], 'w'))"]


def pipeline(code: str | None = None, *, timeout_sec: int = 30, memory_mb: int | None = None) -> ProcessPipeline:
    command = [sys.executable, "-c", code, "{output}"] if code else [*REPORT, "{output}/report.json"]
    return ProcessPipeline("test", "0", command, timeout_sec=timeout_sec, memory_mb=memory_mb, poll_sec=0.05)


def wait(store: JobStore, job_id: str, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get(job_id)
        if job.status in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def store_with(tmp_path: Path, *pipelines: ProcessPipeline) -> JobStore:
    queue = iter(pipelines)
    return JobStore(tmp_path, lambda: next(queue), workers=1)


def upload(store: JobStore, name: str):
    (Path(store.root).parent / "upload.pdf").write_bytes(b"%PDF-1.7\n")
    with (Path(store.root).parent / "upload.pdf").open("rb") as f:
        return store.create(name, [("a.pdf", f)])


def test_hung_job_times_out_and_the_next_job_runs(tmp_path):
    hung = pipeline("import time; time.sleep(60)", timeout_sec=1)
    store = store_with(tmp_path, hung, pipeline())
    first, second = upload(store, "hung"), upload(store, "next")
    started = time.monotonic()
    failed = wait(store, first.id)
    assert failed.status == "failed" and "Превышено время обработки (1 с)" in failed.error
    assert time.monotonic() - started < 10
    assert wait(store, second.id).status == "done"


def test_relative_data_dir(tmp_path, monkeypatch):
    # The default ARM_DATA_DIR is "data": the job process runs in its output folder and still finds both folders.
    monkeypatch.chdir(tmp_path)
    store = JobStore(Path("data"), pipeline, workers=1)
    (tmp_path / "upload.pdf").write_bytes(b"%PDF-1.7\n")
    with (tmp_path / "upload.pdf").open("rb") as f:
        job = store.create("relative", [("a.pdf", f)])
    assert wait(store, job.id).status == "done"


def test_timeout_stops_child_processes(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pid_file)!r}, 'w').write(str(child.pid)); time.sleep(60)"
    )
    store = store_with(tmp_path, pipeline(code, timeout_sec=1))
    assert wait(store, upload(store, "tree").id).status == "failed"
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail("the OCR-like child process outlived the job")


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux only")
def test_memory_limit_stops_the_job(tmp_path):
    store = store_with(tmp_path, pipeline("bytearray(1024 * 1024 * 1024)", memory_mb=256))
    failed = wait(store, upload(store, "heavy").id)
    assert failed.status == "failed" and failed.error == "Превышен лимит памяти (256 МБ)"


def test_rerun_does_not_reuse_the_previous_report(tmp_path):
    exits_without_report = pipeline("pass")
    store = store_with(tmp_path, pipeline(), exits_without_report)
    job = upload(store, "twice")
    assert wait(store, job.id).status == "done"
    store.rerun(job.id)
    again = wait(store, job.id)
    assert again.status == "failed" and again.error == "Конвейер не записал report.json"


def test_progress_file_reaches_the_job(tmp_path):
    code = (
        "import json, sys, time; out = sys.argv[1]; "
        "json.dump({'stage': 'Шаг', 'progress': 0.5}, open(out + '/progress.json', 'w')); time.sleep(1); "
        "json.dump({'kit': {'id': 'x', 'name': ''}}, open(out + '/report.json', 'w'))"
    )
    store = store_with(tmp_path, pipeline(code))
    job = upload(store, "progress")
    deadline = time.monotonic() + 10
    while store.get(job.id).stage != "Шаг" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert store.get(job.id).progress == 0.5
    assert wait(store, job.id).status == "done"


def test_upload_past_the_size_limit_leaves_nothing(tmp_path):
    store = store_with(tmp_path)
    (tmp_path / "big.pdf").write_bytes(b"%PDF-1.7\n" + b"0" * 4096)
    with (tmp_path / "big.pdf").open("rb") as f, pytest.raises(UploadTooLarge):
        store.create("big", [("a.pdf", f)], max_bytes=1024)
    assert list(store.root.iterdir()) == []


def test_default_pipeline_is_the_core_in_a_process(monkeypatch):
    monkeypatch.delenv("ARM_PIPELINE_CMD", raising=False)
    monkeypatch.delenv("ARM_INPROCESS", raising=False)
    monkeypatch.delenv("ARM_CONFIG_DIR", raising=False)
    default = get_pipeline()
    assert isinstance(default, ProcessPipeline) and default.name == "baseline"
    assert default.command[1:4] == ["-m", "kdcheck", "run"]
    assert default.command[default.command.index("--config") + 1] == str(config_dir())
    assert (default.timeout_sec, default.memory_mb) == (3600, 8192)  # configs/api.yaml
    monkeypatch.setenv("ARM_PIPELINE_TIMEOUT", "60")
    monkeypatch.setenv("ARM_JOB_MEMORY_MB", "0")
    assert (get_pipeline().timeout_sec, get_pipeline().memory_mb) == (60, None)
    monkeypatch.setenv("ARM_INPROCESS", "1")
    assert isinstance(get_pipeline(), BaselinePipeline)


def test_report_kit_gets_the_job_identity(tmp_path):
    store = store_with(tmp_path, pipeline())
    job = upload(store, "Имя комплекта")
    assert wait(store, job.id).status == "done"
    report = json.loads(store.report_path(job.id).read_text(encoding="utf-8"))
    assert report["kit"]["id"] == job.id and report["kit"]["name"] == "Имя комплекта"
