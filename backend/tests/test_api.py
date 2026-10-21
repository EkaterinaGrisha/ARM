"""HTTP API: загрузка комплекта, ожидание обработки, отчёт и выгрузки."""

import importlib
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("ARM_DATA_DIR", str(tmp_path_factory.mktemp("data")))
    monkeypatch_module.setenv("ARM_FRONTEND_DIST", str(tmp_path_factory.mktemp("no_frontend") / "dist"))
    import arm_web.main

    main = importlib.reload(arm_web.main)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def wait_done(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.2)
    raise AssertionError("обработка не завершилась")


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["pipeline"] == "baseline"
    assert body["max_upload_mb"] == 2048 and body["max_files"] == 2000


def test_full_cycle(client, demo_kit):
    root, truth = demo_kit
    files = sorted(p for p in root.rglob("*") if p.is_file())
    payload = [("files", (p.name, p.read_bytes())) for p in files]
    data = {"name": "Комплект API", "paths": [p.relative_to(root).as_posix() for p in files]}
    r = client.post("/api/jobs", files=payload, data=data)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["files_count"] == len(files)

    job = wait_done(client, job["id"])
    assert job["status"] == "done", job.get("error")
    assert job["summary"]["discrepancies_total"] == len(truth["discrepancies"])
    assert any(j["id"] == job["id"] for j in client.get("/api/jobs").json())

    report = client.get(f"/api/jobs/{job['id']}/report").json()
    assert report["kit"]["root_designation"] == "АРМ.303411.000"
    paths = {d["path"] for d in report["documents"]}
    assert "Спецификации/АРМ.303411.000 Редуктор колёсный.pdf" in paths  # структура папок сохранена

    xlsx = client.get(f"/api/jobs/{job['id']}/report.xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    assert "attachment" in xlsx.headers["content-disposition"]
    html = client.get(f"/api/jobs/{job['id']}/report.html")
    assert html.status_code == 200 and "Расхождения" in html.text

    doc = next(d for d in report["documents"] if d["kind"] == "part_drawing")
    png = client.get(f"/api/jobs/{job['id']}/documents/{doc['id']}/pages/1.png")
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/jobs/{job['id']}/documents/{doc['id']}/pages/9.png").status_code == 422
    assert client.get(f"/api/jobs/{job['id']}/documents/nope/file").status_code == 404

    assert client.post(f"/api/jobs/{job['id']}/rerun").json()["status"] in ("queued", "running", "done")
    wait_done(client, job["id"])
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_unknown_and_invalid_ids(client):
    assert client.get("/api/jobs/000000000000").status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code == 404
    r = client.post("/api/jobs", data={"name": "x"})
    assert r.status_code == 422 and r.json()["detail"] == "Не выбрано ни одного файла"


def jobs_on_disk(client) -> int:
    return len(client.get("/api/jobs").json())


def test_upload_past_the_size_limit_is_rejected(client, monkeypatch):
    monkeypatch.setenv("ARM_MAX_UPLOAD_MB", "1")
    before = jobs_on_disk(client)
    r = client.post("/api/jobs", files=[("files", ("a.pdf", b"%PDF" + b"0" * 2 * 1024 * 1024))])
    assert r.status_code == 413
    assert r.json()["detail"] == "Комплект больше 1 МБ: разделите его или увеличьте ARM_MAX_UPLOAD_MB"
    assert jobs_on_disk(client) == before


def test_too_many_files_are_rejected_in_russian_past_the_old_1000_cap(client, monkeypatch):
    monkeypatch.setenv("ARM_MAX_FILES", "1000")
    files = [("files", (f"f{i}.txt", b"x")) for i in range(1001)]
    r = client.post("/api/jobs", files=files, data={"paths": [f"kit/f{i}.txt" for i in range(1001)]})
    assert r.status_code == 413
    assert r.json()["detail"] == "Файлов в комплекте: 1001, допустимо не больше 1000. Увеличьте ARM_MAX_FILES"
