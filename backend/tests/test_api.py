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
    assert client.post("/api/jobs", data={"name": "x"}).status_code == 422
