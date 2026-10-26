"""A vision-language model reads title block cells (ADR-0014): the client, the cells, the pipeline.

The model runs on an OpenAI-compatible server; here a stub on this machine plays it, so no model is needed.
"""

import base64
import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from kdcheck.config import Settings
from kdcheck.extract import title_block
from kdcheck.ingest.document import load_document, page_raster
from kdcheck.ingest.pdf import get_backend
from kdcheck.pipeline import BaselinePipeline
from kdcheck.vlm import title_block as vlm_title_block
from kdcheck.vlm.client import Prompt, VlmClient, VlmError

KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"
DRAWING = "Чертежи/АРМ.303411.201 Вал ведомый.pdf"
MODEL = "test/vlm"


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer looks up the host name here, which can take half a minute on macOS; the tests need no name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class StubServer:
    """``/models`` lists ``MODEL``; ``/chat/completions`` answers ``answers[label]`` and keeps the requests.

    With ``in_reasoning`` the answer comes under ``reasoning_content`` and the content is empty, as LM Studio files
    the answer of Qwen3.6 when a JSON schema bounds it.
    """

    def __init__(self) -> None:
        self.answers: dict[str, str] = {}
        self.requests: list[dict] = []
        self.in_reasoning = False
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._send({"object": "list", "data": [{"id": MODEL, "object": "model"}]})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(body)
                prompt = body["messages"][0]["content"][0]["text"]
                label = next((k for k in stub.answers if f"«{k}»" in prompt), None)
                content = stub.answers.get(label, json.dumps({"text": ""}))
                message = {"role": "assistant", "content": content}
                if stub.in_reasoning:
                    message = {"role": "assistant", "content": "", "reasoning_content": content}
                self._send({"choices": [{"index": 0, "message": message}]})

            def _send(self, payload: dict) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = _Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def say(self, label: str, text: str) -> None:
        self.answers[label] = json.dumps({"text": text}, ensure_ascii=False)


@pytest.fixture
def server():
    stub = StubServer()
    yield stub
    stub.httpd.shutdown()


def _settings(settings: Settings, url: str, **vlm: object) -> Settings:
    """The test settings with the model on at ``url``; frozen models are copied, not changed."""
    engine = settings.models.vlm.model_copy(update={"enabled": True, "base_url": url, "model": MODEL, **vlm})
    return settings.model_copy(update={"models": settings.models.model_copy(update={"vlm": engine})})


def _client(settings: Settings, url: str) -> VlmClient:
    return VlmClient(_settings(settings, url).models.vlm, Prompt("cell.v1", "Ячейка «{label}». Ответ — JSON {text}."))


def _closed_port() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


def _drawing(settings: Settings, eskd):
    doc = load_document(KIT / DRAWING, KIT, settings)
    page = doc.pages[0]
    geom = title_block.locate(page, settings.forms)
    return doc, page, geom, title_block.parse(doc, page, geom, settings.forms, eskd)


def test_client_sends_the_cell_and_asks_for_json(settings, server):
    from PIL import Image

    server.say("Материал", "Сталь 45 ГОСТ 1050-2013")
    client = _client(settings, server.url)
    assert client.unavailable() is None
    assert client.read(Image.new("L", (40, 20), 255), "Материал") == "Сталь 45 ГОСТ 1050-2013"
    body = server.requests[-1]
    assert (body["model"], body["temperature"], body["seed"]) == (MODEL, 0, settings.models.vlm.seed)
    assert body["response_format"]["json_schema"]["schema"]["required"] == ["text"]
    text, image = body["messages"][0]["content"]
    assert text["text"] == "Ячейка «Материал». Ответ — JSON {text}."  # {label} replaced, other braces kept
    assert base64.b64decode(image["image_url"]["url"].split(",", 1)[1]).startswith(b"\x89PNG")
    assert client.version.startswith("cell.v1 sha256:")


def test_a_reply_in_another_form_is_no_reading(settings, server):
    from PIL import Image

    server.answers["Масса"] = "Масса детали — 4,2 кг"  # free text: a reasoning model ignoring the schema
    with pytest.raises(VlmError) as caught:
        _client(settings, server.url).read(Image.new("L", (40, 20), 255), "Масса")
    assert not caught.value.fatal


def test_an_answer_filed_under_reasoning_counts_only_as_the_whole_json(settings, server):
    from PIL import Image

    server.in_reasoning = True
    server.say("Масса", "0,22")
    client = _client(settings, server.url)
    assert client.read(Image.new("L", (40, 20), 255), "Масса") == "0,22"
    server.answers["Масса"] = 'Посмотрю на ячейку: там {"text": "0,22"}'  # reasoning, not the answer
    with pytest.raises(VlmError):
        client.read(Image.new("L", (40, 20), 255), "Масса")


def test_a_server_that_does_not_answer_is_fatal(settings):
    from PIL import Image

    client = _client(settings, _closed_port())
    assert "не отвечает" in client.unavailable()
    with pytest.raises(VlmError) as caught:
        client.read(Image.new("L", (40, 20), 255), "Масса")
    assert caught.value.fatal


def test_a_model_the_server_does_not_serve(settings, server):
    client = VlmClient(_settings(settings, server.url, model="other/model").models.vlm, Prompt("cell.v1", "«{label}»"))
    assert client.unavailable() == f"на сервере {server.url} нет модели other/model"


def test_the_model_fills_a_cell_the_rules_left_empty(settings, eskd, server):
    server.say("Материал", "Сталь 45 ГОСТ 1050-2013")
    server.say("Масса", "9,9")  # not asked: the rules read the mass
    on = _settings(settings, server.url)
    doc, page, geom, tb = _drawing(on, eskd)
    tb.material = None
    client = VlmClient(on.models.vlm, Prompt("cell.v1", "«{label}»"))
    raster = page_raster(doc, page, on, get_backend(on.models.pdf.backend))
    notes = vlm_title_block.read_cells(doc, page, geom, tb, client=client, raster=raster, settings=on, eskd=eskd)
    assert notes == [] and client.calls == 1
    assert tb.material.value == "Сталь 45 ГОСТ 1050-2013" and tb.mass.value == "4,2"
    source = tb.material.source
    assert (source.method, source.engine, source.engine_version) == ("vlm", MODEL, client.version)
    assert tb.material.confidence == on.ingest.confidence.vlm
    cell = page.norm_bbox(geom.rect(on.forms.title_block.forms[1].cells.material))
    assert source.bbox == cell  # the evidence is the template cell the model was shown


def test_verify_marks_a_different_reading_as_a_conflict(settings, eskd, server):
    server.say("Масса", "4.2")  # the same value written with a point
    server.say("Масштаб", "Масштаб 1:2")  # the rules read 1:1
    on = _settings(settings, server.url, verify=True, fields={"mass": "Масса", "scale": "Масштаб"})
    doc, page, geom, tb = _drawing(on, eskd)
    client = VlmClient(on.models.vlm, Prompt("cell.v1", "«{label}»"))
    raster = page_raster(doc, page, on, get_backend(on.models.pdf.backend))
    notes = vlm_title_block.read_cells(doc, page, geom, tb, client=client, raster=raster, settings=on, eskd=eskd)
    assert (tb.mass.value, tb.mass.status) == ("4,2", None)
    assert (tb.scale.value, tb.scale.status, tb.scale.source.method) == ("1:1", "conflict", "text_layer")
    assert notes == ["Основная надпись, «Масштаб»: прочитано «1:1», модель прочла «1:2»"]


def _kit_of_one_assembly_drawing(folder: Path) -> None:
    name = "АРМ.303411.000 СБ.pdf"
    (folder / "Чертежи").mkdir()
    (folder / "Чертежи" / name).write_bytes((KIT / "Чертежи" / name).read_bytes())


def test_pipeline_records_the_model_and_marks_its_values_for_review(settings, server, tmp_path):
    _kit_of_one_assembly_drawing(tmp_path)
    server.say("Материал", "Сталь 45")  # an assembly drawing has no material: the cell is empty, the model is asked
    # Vector pages stand in for scans, so the test needs no Tesseract.
    on = _settings(settings, server.url, methods=("text_layer",))
    report = BaselinePipeline(on).run(tmp_path, kit_id="t", kit_name="t")
    material = report.parts[0].title_block.material
    assert (material.value, material.source.method, material.status) == ("Сталь 45", "vlm", "low_confidence")
    assert any(e.name == MODEL and e.version.startswith(on.models.vlm.prompt) for e in report.kit.engines)


def test_a_dead_server_leaves_the_run_to_the_rules(settings, tmp_path):
    _kit_of_one_assembly_drawing(tmp_path)
    on = _settings(settings, _closed_port(), methods=("text_layer",))
    report = BaselinePipeline(on).run(tmp_path, kit_id="t", kit_name="t")
    assert report.parts[0].designation == "АРМ.303411.000 СБ"
    assert report.parts[0].title_block.material is None
    assert all(e.name != MODEL for e in report.kit.engines)
    assert any("не ответила" in e.message and "без неё" in e.message for e in report.log)
