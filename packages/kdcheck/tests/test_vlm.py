"""A vision-language model reads title block cells (ADR-0014): the client, the cells, the pipeline.

The model runs on an OpenAI-compatible server; here a stub on this machine plays it, so no model is needed.
"""

import base64
import io
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
from kdcheck.vlm.client import Prompt, VlmClient, VlmError, load_prompt

KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"
DRAWING = "Чертежи/АРМ.303411.201 Вал ведомый.pdf"
MODEL = "test/vlm"


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer looks up the host name here, which can take half a minute on macOS; the tests need no name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class StubServer:
    """``/models`` lists ``MODEL``; ``/chat/completions`` answers ``answers[label]`` and keeps the requests; a key
    found in a prompt without a label answers it too.

    With ``in_reasoning`` the answer comes under ``reasoning_content`` and the content is empty, as LM Studio files
    the answer of Qwen3.6 when a JSON schema bounds it.
    """

    def __init__(self) -> None:
        self.answers: dict[str, str] = {}
        self.turns: dict[str, list[str]] = {}
        self.requests: list[dict] = []
        self.in_reasoning = False
        self.redirect: str | None = None
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if stub.redirect:
                    self.send_response(302)
                    self.send_header("Location", stub.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._send({"object": "list", "data": [{"id": MODEL, "object": "model"}]})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(body)
                prompt = body["messages"][0]["content"][0]["text"]
                label = next((k for k in stub.answers if f"«{k}»" in prompt), None)
                label = label or next((k for k in stub.answers if k in prompt), None)  # a prompt with no «{label}»
                content = stub.answers.get(label, json.dumps({"text": ""}))
                if label in stub.turns and stub.turns[label]:  # one reply a request, in turn
                    content = json.dumps({"text": stub.turns[label].pop(0)}, ensure_ascii=False)
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

    def say_in_turn(self, label: str, *texts: str) -> None:
        self.answers.setdefault(label, json.dumps({"text": ""}))
        self.turns[label] = list(texts)


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


def test_the_client_ignores_proxies_so_the_image_stays_on_this_machine(settings, server, monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, _closed_port())
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert _client(settings, server.url).unavailable() is None


def test_the_client_does_not_follow_a_redirect(settings, server):
    target = StubServer()
    try:
        server.redirect = target.url + "/models"
        reason = _client(settings, server.url).unavailable()
        assert reason is not None and "302" in reason
        assert not target.requests
    finally:
        target.httpd.shutdown()


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
    server.say("Масса", "9,9")  # not asked without verify: the rules read the mass
    on = _settings(settings, server.url, verify=False)
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


def _verified(settings, eskd, server, conflict: str):
    server.say("Масса", "4.2")  # the same value written with a point
    server.say("Масштаб", "Масштаб 1:2")  # the rules read 1:1
    on = _settings(settings, server.url, verify=True, conflict=conflict, fields={"mass": "Масса", "scale": "Масштаб"})
    doc, page, geom, tb = _drawing(on, eskd)
    client = VlmClient(on.models.vlm, Prompt("cell.v1", "«{label}»"))
    raster = page_raster(doc, page, on, get_backend(on.models.pdf.backend))
    notes = vlm_title_block.read_cells(doc, page, geom, tb, client=client, raster=raster, settings=on, eskd=eskd)
    assert (tb.mass.value, tb.mass.status, tb.mass.source.method) == ("4,2", None, "text_layer")  # they agree
    return on, page, geom, tb, client, notes


def test_a_conflict_keeps_the_reading_of_the_model_with_its_evidence(settings, eskd, server):
    """ADR-0019: the model reads the cells; the other reading stays in the notes, the cell goes to a check."""
    on, page, geom, tb, client, notes = _verified(settings, eskd, server, "model")
    assert (tb.scale.value, tb.scale.status, tb.scale.raw) == ("1:2", "conflict", "Масштаб 1:2")
    source = tb.scale.source
    assert (source.method, source.engine, source.engine_version) == ("vlm", MODEL, client.version)
    assert source.bbox == page.norm_bbox(geom.rect(on.forms.title_block.forms[1].cells.scale))
    assert notes == ["Основная надпись, «Масштаб»: прочитано «1:1», модель прочла «1:2»; в отчёте — прочтение модели"]


def test_a_conflict_may_keep_the_reading_before_the_model(settings, eskd, server):
    _, _, _, tb, _, notes = _verified(settings, eskd, server, "ocr")
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


@pytest.mark.parametrize(
    ("reading", "want", "named"),
    [
        ("АБМ.303411.201", "АРМ.303411.201", True),  # a letter misread: the designation of the file name (ADR-0022)
        ("АРМ.303411.204", "АРМ.303411.204", False),  # a digit read as another: the reading stays
    ],
)
def test_a_designation_the_model_misread_is_decided_by_the_file_name(settings, server, tmp_path, reading, want, named):
    (tmp_path / "Чертежи").mkdir()
    (tmp_path / DRAWING).write_bytes((KIT / DRAWING).read_bytes())
    server.say("Обозначение документа", reading)
    on = _settings(settings, server.url, methods=("text_layer",))
    report = BaselinePipeline(on).run(tmp_path, kit_id="t", kit_name="t")
    designation = report.parts[0].title_block.designation
    assert (report.parts[0].designation, report.documents[0].designation) == (want, want)
    assert (designation.value, designation.raw, designation.status) == (want, reading, "conflict")
    assert designation.source.method == "vlm"
    notes = report.documents[0].notes
    assert any("в имени файла «АРМ.303411.201»; в отчёте" in n for n in notes) is named
    assert any(n.startswith("Обозначение в имени файла") for n in notes) is not named


def test_the_same_cell_is_asked_once_with_a_cache(settings, server, tmp_path):
    """The key is the request with the hash of the pixels: another cell, label or prompt asks the server again; the
    readings asked for are counted all the same, so a run reports the same number with a cache or without."""
    from kdcheck import cache
    from PIL import Image

    server.say("Материал", "Сталь 45 ГОСТ 1050-2013")
    client = _client(settings, server.url)
    cell = Image.new("L", (40, 20), 255)
    with cache.use(tmp_path):
        assert client.read(cell, "Материал") == "Сталь 45 ГОСТ 1050-2013"
        assert client.read(cell.copy(), "Материал") == "Сталь 45 ГОСТ 1050-2013"
        client.read(cell, "Масса")
        other = Image.new("L", (40, 20), 255)
        other.putpixel((5, 5), 0)
        client.read(other, "Материал")
    assert len(server.requests) == 3 and client.calls == 4
    records = [r.read_text(encoding="utf-8") for r in (tmp_path / "vlm").rglob("*.json")]
    assert len(records) == 3 and all("base64" not in r and "sha256:" in r for r in records)  # no image is kept


def test_a_failed_reading_is_not_kept(settings, tmp_path):
    from kdcheck import cache
    from PIL import Image

    client = VlmClient(_settings(settings, _closed_port()).models.vlm, Prompt("cell.v1", "«{label}»"))
    with cache.use(tmp_path), pytest.raises(VlmError):
        client.read(Image.new("L", (10, 10), 255), "Масса")
    assert not list(tmp_path.rglob("*.json"))


def _rows_on_a_table(settings, eskd, monkeypatch, server, conflict="model", name_lines=0):
    """A page with a table of one row placed by its lines: the layout is given, so the test needs no raster.
    ``name_lines``: lines of a name OCR read in the row and the bands below it, as a name carried to the next line."""
    from kdcheck.layout import raster
    from kdcheck.schemas import Extracted, SourceRef, SpecRow
    from kdcheck.vlm import spec_rows
    from PIL import Image

    on = _settings(settings, server.url, conflict=conflict)
    doc, page, _, _ = _drawing(on, eskd)
    scale = 0.5  # page units per pixel
    image = Image.new("L", (int(page.width / scale), int(page.height / scale)), 255)
    columns = {"designation": (20.0, 90.0), "name": (90.0, 160.0)}
    table = raster.Table(left=100.0, s=4.0, columns=columns, rows=[(400.0, 432.0), (432.0, 464.0), (464.0, 496.0)])
    corner = raster.Corner(turn=raster.Turn(pivot=(0.0, 0.0)), level=128, block=None)  # type: ignore[arg-type]
    monkeypatch.setattr(spec_rows.raster, "find_corner", lambda *a: corner)
    monkeypatch.setattr(spec_rows.raster, "find_table", lambda *a: table)
    band = page.norm_bbox((100 * scale, 405 * scale, 400 * scale, 427 * scale))
    source = SourceRef(document_id=doc.id, page=page.number, bbox=band, text="?", method="ocr")
    row = SpecRow(id="r1", position=Extracted(value="3", raw="3"), source=source)
    row.designation = Extracted(value="АРМ.7584.22.002", raw="АРМ.7584.22.002", source=source)
    if name_lines:
        lines = ["Тяга рулевая с", "наконечниками"][:name_lines]
        words = page.norm_bbox((280 * scale, 405 * scale, 400 * scale, (405 + 32 * (name_lines - 1) + 22) * scale))
        name = SourceRef(document_id=doc.id, page=page.number, bbox=words, text=" ".join(lines), method="ocr")
        row.name = Extracted(value=" ".join(lines), raw=" ".join(lines), source=name)
    client = VlmClient(on.models.vlm, Prompt("rows.v1", "Графа «{label}»"))
    notes = spec_rows.read_rows(
        doc, page, [row], client=client, image=(image, (0.0, 0.0), scale), settings=on, eskd=eskd
    )
    return row, notes, client, page


def test_the_model_reads_the_cells_of_a_specification_row(settings, eskd, monkeypatch, server):
    """ADR-0021: a different designation is a conflict that keeps the model's reading, an empty name is filled."""
    server.say("Обозначение", "АРМ.758422.002")
    server.say("Наименование", "Гайка специальная")
    row, notes, client, page = _rows_on_a_table(settings, eskd, monkeypatch, server)
    assert (row.designation.value, row.designation.status, row.designation.source.method) == (
        "АРМ.758422.002",
        "conflict",
        "vlm",
    )
    assert (row.name.value, row.name.status, row.name.source.engine_version) == (
        "Гайка специальная",
        None,
        client.version,
    )
    assert notes == [
        "Спецификация, строка поз. 3, «Обозначение»: прочитано «АРМ.7584.22.002», модель прочла «АРМ.758422.002»; "
        "в отчёте — прочтение модели"
    ]
    x0, y0, x1, y1 = row.designation.source.bbox  # the cell of the column in the band, not the words OCR found
    assert y0 < row.source.bbox[1] and y1 > row.source.bbox[3] and x1 < row.name.source.bbox[0] + 1e-9
    assert [r["messages"][0]["content"][0]["text"] for r in server.requests] == [
        "Графа «Обозначение»",
        "Графа «Наименование»",
    ]


def test_the_model_reads_each_line_of_a_name_carried_to_the_next_line(settings, eskd, monkeypatch, server):
    """OCR joined two lines of a name into the row; the model reads both cells, not the first one alone."""
    server.say("Обозначение", "АРМ.7584.22.002")
    server.say_in_turn("Наименование", "Тяга рулевая с", "наконечниками")
    row, notes, client, page = _rows_on_a_table(settings, eskd, monkeypatch, server, name_lines=2)
    assert (row.name.value, row.name.source.method, notes) == ("Тяга рулевая с наконечниками", "ocr", [])
    assert client.calls == 3  # the designation, then the name in the row and in the band below it, not the third


def test_a_name_of_one_line_is_read_in_its_band_alone(settings, eskd, monkeypatch, server):
    server.say("Обозначение", "АРМ.7584.22.002")
    server.say_in_turn("Наименование", "Тяга рулевая", "наконечниками")
    row, notes, client, _ = _rows_on_a_table(settings, eskd, monkeypatch, server, name_lines=1)
    assert (row.name.value, row.name.status, row.name.source.method) == ("Тяга рулевая", "conflict", "vlm")
    assert client.calls == 2


def test_a_row_conflict_may_keep_the_reading_of_ocr(settings, eskd, monkeypatch, server):
    server.say("Обозначение", "АРМ.758422.002")
    row, notes, _, _ = _rows_on_a_table(settings, eskd, monkeypatch, server, conflict="ocr")
    assert (row.designation.value, row.designation.status, row.designation.source.method) == (
        "АРМ.7584.22.002",
        "conflict",
        "ocr",
    )
    assert notes[0].endswith("модель прочла «АРМ.758422.002»")


def test_a_page_without_a_table_placed_by_its_lines_asks_nothing(settings, eskd, server):
    from kdcheck.vlm import spec_rows
    from PIL import Image

    on = _settings(settings, server.url)
    doc, page, _, _ = _drawing(on, eskd)  # a drawing: no table of a specification on it
    client = VlmClient(on.models.vlm, Prompt("rows.v1", "«{label}»"))
    image = (Image.new("L", (200, 140), 255), (0.0, 0.0), page.width / 200)
    assert spec_rows.read_rows(doc, page, [], client=client, image=image, settings=on, eskd=eskd) == []
    assert client.calls == 0 and server.requests == []


BLOCK = "блок технических требований"  # words of prompts/tech_requirements.v1.txt
READ_BLOCK = """Технические требования
1. 250...280 HB.
2. Поверхности Б закалить ТВЧ h 1,5...2,0 мм,
48...53 HRC.
3. Покрытие Хим.Окс.прм."""


def _block_lines(settings: Settings, eskd):
    """The demo drawing and the lines above its title block, taken as OCR would take them."""
    from kdcheck.extract import tech_requirements

    doc, page, geom, _ = _drawing(settings, eskd)
    return doc, page, tech_requirements.requirement_lines(page, geom, settings.forms.tech_requirements, numbered=False)


def test_the_model_reads_the_technical_requirements_block_into_items(settings, eskd, server):
    from kdcheck.vlm import tech_requirements as vlm_tech_requirements
    from PIL import Image

    server.say(BLOCK, READ_BLOCK)
    on = _settings(settings, server.url)
    doc, page, lines = _block_lines(on, eskd)
    scale = 0.5
    image = Image.new("L", (int(page.width / scale), int(page.height / scale)), 255)
    client = VlmClient(on.models.vlm, load_prompt(on.models.vlm.requirements.prompt))
    items = vlm_tech_requirements.read_block(
        doc, page, lines, client=client, raster=(image, (0.0, 0.0), scale), settings=on
    )
    assert [i.value for i in items] == [
        "250...280 HB.",
        "Поверхности Б закалить ТВЧ h 1,5...2,0 мм, 48...53 HRC.",
        "Покрытие Хим.Окс.прм.",
    ]
    pad = on.models.vlm.requirements.pad_mm * page.mm
    block = (
        min(ln.bbox[0] for ln in lines) - pad,
        min(ln.bbox[1] for ln in lines) - pad,
        max(ln.bbox[2] for ln in lines) + pad,
        max(ln.bbox[3] for ln in lines) + pad,
    )
    source = items[1].source
    assert source.method == "vlm" and source.engine == MODEL
    assert (items[1].raw, source.text) == ("2. Поверхности Б закалить ТВЧ h 1,5...2,0 мм, 48...53 HRC.",) * 2
    # an item has the rows of OCR its lines read; «3. Покрытие …» is like no row, so its item has the block
    assert [i.source.bbox for i in items] == [
        page.norm_bbox(lines[0].bbox),
        page.norm_bbox(lines[1].bbox),
        page.norm_bbox(block),
    ]
    # 144 dpi at 0.5 page units per pixel: the crop is enlarged to the 300 dpi of the settings
    crop = server.requests[0]["messages"][0]["content"][1]["image_url"]["url"]
    width = Image.open(io.BytesIO(base64.b64decode(crop.split(",", 1)[1]))).width
    block_width = (max(ln.bbox[2] for ln in lines) - min(ln.bbox[0] for ln in lines) + 2 * pad) / scale
    assert width == pytest.approx(block_width * 300 / 144, abs=2)


def test_pipeline_takes_the_items_the_model_read_from_the_block(settings, server, tmp_path):
    (tmp_path / "Чертежи").mkdir()
    (tmp_path / DRAWING).write_bytes((KIT / DRAWING).read_bytes())
    server.say(BLOCK, READ_BLOCK)
    on = _settings(settings, server.url, methods=("text_layer",))
    report = BaselinePipeline(on).run(tmp_path, kit_id="t", kit_name="t")
    part = report.parts[0]
    assert len(part.requirements) == 3 and {i.source.method for i in part.requirements} == {"vlm"}
    hardness = [p for p in part.parameters if p.category == "hardness" and p.unit == "HB"]
    assert [(p.value_min, p.value_max) for p in hardness] == [(250.0, 280.0)]  # the model's reading, not the layer's
    assert any(e.version.startswith(on.models.vlm.requirements.prompt) for e in report.kit.engines)


def test_a_block_the_model_did_not_read_keeps_the_items_of_ocr(settings, server, tmp_path):
    (tmp_path / "Чертежи").mkdir()
    (tmp_path / DRAWING).write_bytes((KIT / DRAWING).read_bytes())
    server.answers[BLOCK] = "не JSON"
    on = _settings(settings, server.url, methods=("text_layer",))
    report = BaselinePipeline(on).run(tmp_path, kit_id="t", kit_name="t")
    part = report.parts[0]
    assert len(part.requirements) == 4 and {i.source.method for i in part.requirements} == {"text_layer"}
    assert any(n.startswith("Модель не прочитала технические требования") for n in report.documents[0].notes)


def test_an_empty_answer_keeps_the_items_of_ocr(settings, server, tmp_path):
    (tmp_path / "Чертежи").mkdir()
    (tmp_path / DRAWING).write_bytes((KIT / DRAWING).read_bytes())
    on = _settings(settings, server.url, methods=("text_layer",))
    report = BaselinePipeline(on).run(tmp_path, kit_id="t", kit_name="t")
    assert [i.source.method for i in report.parts[0].requirements] == ["text_layer"] * 4
