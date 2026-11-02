"""Local multimodal models on the same tasks: real sheets, requirement blocks, title block cells.

Each model is loaded alone on the server of this machine, LM Studio by default, and reads the same images:

- ``sheets``: the scans of the gold set (eval/gold/sheets.json), whole. A model given with ``--model`` answers by a
  JSON schema with the fields of the title block; an OCR model given with ``--text-model`` returns the text of the
  sheet, and the value is looked for in that text;
- ``requirements``: blocks of technical requirements of synthetic rasters and scans, cut by the box of the truth, so
  finding a block is not measured. The text goes through ``kdcheck.params.parameters.Parser``;
- ``cells``: title block values of the same documents, cut by the box of the truth.

Tesseract reads the same images as the baseline. Values compare by the rule of ``eval/gold.py``. Replies are kept in
data/vlm_bench, outside git: a run continues where it stopped, and ``--score`` recounts them with no server. Writes
eval/results/vlm-bench.json and eval/results/vlm-bench.md.

    uv run python eval/vlm_bench.py --model qwen/qwen3.6-27b --text-model paddleocr-vl=bench_ocr_paddle.v1
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from gold import norm
from kdcheck.params.parameters import Parser
from kdcheck.run import load_settings
from kdcheck.schemas import Extracted
from kdcheck.vlm.client import load_prompt
from PIL import Image
from synth.truth import Box, DrawingTruth, KitTruth

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "gold" / "sheets.json"
SHEETS = ROOT / "data" / "gold_sheets"
KITS = ROOT / "data" / "synth" / "kits"
RAW = ROOT / "data" / "vlm_bench"
PROMPTS = ROOT / "prompts"
OUT = Path(__file__).resolve().parent / "results"
LMS = Path.home() / ".lmstudio" / "bin" / "lms"

FIELDS = ("designation", "name", "material", "mass", "scale", "litera")
REQUIREMENT_CATEGORIES = ("general_tolerance", "heat_treatment", "hardness", "coating")  # written only in the block
LONG_SIDE = 2000  # px: a whole sheet goes to a model at this size
TARGET_DPI = 300  # a crop of a coarser image is brought to this
BLOCK_PAD, CELL_PAD = 0.01, 0.004  # share of the sheet around the box of the truth
CONTEXT = 8192  # tokens of the context of a loaded model
MAX_TOKENS = 2048
TIMEOUT = 400  # s, one reading
MAX_ERRORS = 3  # readings of a task in a row that fail: the model leaves that task

BASELINE = "tesseract"
TEXT_SCHEMA = {"text": {"type": "string"}}
FIELDS_SCHEMA = {name: {"type": "string"} for name in FIELDS}


@dataclass(frozen=True)
class Profile:
    """A model on the server. ``prompt`` is set for an OCR model, which takes one instruction and returns text."""

    name: str
    prompt: str | None = None

    @property
    def label(self) -> str:
        """The model, and the prompt of an OCR model: one model may be measured both ways."""
        return self.name if self.prompt is None else f"{self.name}={self.prompt}"

    @property
    def slug(self) -> str:
        return re.sub(r"[^\w.-]+", "_", self.label)


@dataclass
class Item:
    task: str
    key: str
    image: Image.Image
    doc: DrawingTruth
    field: str | None = None


@dataclass
class Tally:
    counts: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0
    errors: int = 0
    distance: float = 0.0


def distance(a: str, b: str) -> float:
    """Edits between the strings over the length of the longer one: the CER of a cell."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        previous, row[0] = row[0], i
        for j, y in enumerate(b, start=1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (x != y))
    return row[-1] / max(len(a), len(b), 1)


def prompt_text(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8").strip()


def prompt_version(name: str) -> str:
    """The start of the SHA-256 of the prompt file, the same as ``kdcheck.vlm`` writes to a report."""
    return load_prompt(name, PROMPTS).version.rpartition(":")[2]


def schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


def page_image(path: Path, page: int) -> tuple[Image.Image, float]:
    """A page in grey and its resolution."""
    if path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(path)
        return pdf[page - 1].render(scale=TARGET_DPI / 72).to_pil().convert("L"), TARGET_DPI
    image = Image.open(path)
    image.seek(page - 1)
    return image.convert("L"), float(image.info.get("dpi", (TARGET_DPI,))[0]) or TARGET_DPI


def fit(image: Image.Image) -> Image.Image:
    k = LONG_SIDE / max(image.size)
    return image.resize((round(image.width * k), round(image.height * k)), Image.Resampling.LANCZOS) if k < 1 else image


def cut(image: Image.Image, dpi: float, boxes: list[Box], pad: float) -> Image.Image:
    w, h = image.size
    x0, y0 = min(b.bbox[0] for b in boxes) - pad, min(b.bbox[1] for b in boxes) - pad
    x1, y1 = max(b.bbox[2] for b in boxes) + pad, max(b.bbox[3] for b in boxes) + pad
    crop = image.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    k = max(1.0, TARGET_DPI / dpi)
    return crop.resize((round(crop.width * k), round(crop.height * k)), Image.Resampling.LANCZOS)


def corpus(requirements: int, cell_docs: int) -> list[Item]:
    items: list[Item] = []
    gold = KitTruth.read(GOLD)
    for name, doc in gold.documents.items():
        if isinstance(doc, DrawingTruth) and doc.origin != "vector":
            items.append(Item("sheets", name, fit(page_image(SHEETS / name, 1)[0]), doc))
    blocks = cells = 0
    for path in sorted(KITS.glob("kit_*_truth.json")):
        kit = KITS / path.name.removesuffix("_truth.json")
        for doc_path, doc in KitTruth.read(path).documents.items():
            usable = isinstance(doc, DrawingTruth) and doc.origin != "vector" and doc.requirement_boxes
            if not usable or not (kit / doc_path).exists() or (blocks >= requirements and cells >= cell_docs):
                continue
            key = f"{kit.name}/{doc_path}"
            page = doc.requirement_boxes[0].page
            image, dpi = page_image(kit / doc_path, page)
            if blocks < requirements:
                boxes = [b for b in doc.requirement_boxes if b.page == page]
                items.append(Item("requirements", key, cut(image, dpi, boxes, BLOCK_PAD), doc))
                blocks += 1
            if cells < cell_docs:
                for name in FIELDS:
                    box, want = doc.boxes.get(name), getattr(doc, name)
                    if box is not None and box.page == page and want not in (None, "", "См. табл."):
                        items.append(Item("cells", f"{key}#{name}", cut(image, dpi, [box], CELL_PAD), doc, name))
                cells += 1
    return items


def tesseract(item: Item) -> str:
    lang, psm = {"sheets": ("rus+eng", "11"), "requirements": ("rus+eng", "6"), "cells": ("rus", "6")}[item.task]
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        item.image.save(f.name)
        run = subprocess.run(
            ["tesseract", f.name, "stdout", "-l", lang, "--psm", psm], capture_output=True, text=True, timeout=120
        )
    return run.stdout


def ask(url: str, model: str, prompt: str, image: Image.Image, answer: dict[str, Any] | None) -> str:
    png = io.BytesIO()
    image.save(png, "PNG")
    picture = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii")
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "seed": 7,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": picture}}],
            }
        ],
    }
    if answer is not None:
        body["response_format"] = answer
    request = urllib.request.Request(
        f"{url}/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        message = json.loads(response.read())["choices"][0]["message"]
    return message.get("content") or message.get("reasoning_content") or ""


def request_for(profile: Profile, item: Item, labels: dict[str, str]) -> tuple[str, dict[str, Any] | None]:
    if profile.prompt is not None:
        return prompt_text(profile.prompt), None
    if item.task == "sheets":
        return prompt_text("bench_sheet_fields.v1"), schema(FIELDS_SCHEMA)
    if item.task == "requirements":
        return prompt_text("bench_requirements.v1"), schema(TEXT_SCHEMA)
    assert item.field is not None
    return prompt_text("title_block_cell.v1").replace("{label}", labels[item.field]), schema(TEXT_SCHEMA)


def replies(profile: Profile) -> dict[tuple[str, str], dict[str, Any]]:
    path = RAW / f"{profile.slug}.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {(row["task"], row["key"]): row for row in rows}


def load(profile: Profile) -> str | None:
    """Load the model alone; why it failed, or None. Two models on a server have cost a run: one at a time."""
    subprocess.run([str(LMS), "unload", "--all"], capture_output=True, timeout=120)
    run = subprocess.run(
        [str(LMS), "load", profile.name, "-y", "--context-length", str(CONTEXT)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    return None if run.returncode == 0 else (run.stderr or run.stdout)[-300:]


def run_model(profile: Profile, items: list[Item], url: str, labels: dict[str, str], budget: float) -> None:
    done = replies(profile)
    todo = [item for item in items if (item.task, item.key) not in done or "error" in done[item.task, item.key]]
    if not todo:
        return
    RAW.mkdir(parents=True, exist_ok=True)
    if profile.name != BASELINE and (failure := load(profile)):
        print(f"{profile.name}: не загрузилась: {failure}", flush=True)
        return
    start = time.monotonic()
    failed: Counter[str] = Counter()  # readings in a row that failed, by task: one task may be beyond a server
    with (RAW / f"{profile.slug}.jsonl").open("a", encoding="utf-8") as out:
        for item in todo:
            if time.monotonic() - start > budget * 60:
                print(f"{profile.name}: бюджет {budget:g} мин исчерпан", flush=True)
                break
            if failed[item.task] >= MAX_ERRORS:
                continue
            row: dict[str, Any] = {"task": item.task, "key": item.key}
            begun = time.monotonic()
            try:
                if profile.name == BASELINE:
                    row["reply"] = tesseract(item)
                else:
                    prompt, answer = request_for(profile, item, labels)
                    row["reply"] = ask(url, profile.name, prompt, item.image, answer)
                failed[item.task] = 0
            except (urllib.error.URLError, TimeoutError, ConnectionError, KeyError, ValueError) as exc:
                row["error"] = str(exc)
                failed[item.task] += 1
                if failed[item.task] >= MAX_ERRORS:
                    print(f"{profile.name}, {item.task}: {MAX_ERRORS} ошибки подряд: {exc}", flush=True)
            row["sec"] = round(time.monotonic() - begun, 2)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
    print(f"{profile.name}: {time.monotonic() - start:.0f} с", flush=True)


def plain(reply: str) -> str:
    """The text of a reply, without the location tokens PaddleOCR-VL puts after each line of a page."""
    return re.sub(r"<\|LOC_\d+\|>", " ", reply)


def answer_of(reply: str) -> dict[str, Any]:
    """The JSON of a reply; a model may wrap it in words."""
    found = re.search(r"\{.*\}", reply, re.DOTALL)
    try:
        value = json.loads(found.group(0)) if found else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def score_sheet(item: Item, reply: str, structured: bool, tally: Tally) -> None:
    fields = answer_of(reply) if structured else {}
    text = norm(plain(reply))
    for name in FIELDS:
        want = getattr(item.doc, name)
        if want in (None, ""):
            tally.counts["blank"] += 1
            tally.counts["invented"] += bool(str(fields.get(name) or "").strip())
            continue
        tally.counts["total"] += 1
        tally.counts["right"] += structured and norm(fields.get(name)) == norm(want)
        tally.counts["in_reply"] += norm(want) in text


def score_block(item: Item, reply: str, structured: bool, parser: Parser, tally: Tally, ids: Any) -> None:
    text = str(answer_of(reply).get("text") or "") if structured else plain(reply)
    found = []
    for line in re.split(r"\n(?=\s*\S{0,1}\s?\d{1,2}\s?[.)])", text):
        found += parser.requirement(Extracted(value=" ".join(line.split()), confidence=0.7), lambda: f"p{next(ids)}")
    ranges = {(p.category, p.value_min, p.value_max) for p in found}
    used: set[int] = set()
    for want in item.doc.parameters:
        if want.category not in REQUIREMENT_CATEGORIES:
            continue
        tally.counts["total"] += 1
        for i, got in enumerate(found):
            hay = norm(f"{got.name} {got.qualifier or ''} {got.text}")
            if i not in used and got.category == want.category and norm(want.name) in hay:
                used.add(i)
                tally.counts["right"] += 1
                break
        if want.value_min is not None:
            tally.counts["ranges"] += 1
            tally.counts["ranges_right"] += (want.category, want.value_min, want.value_max) in ranges


def score_cell(item: Item, reply: str, structured: bool, tally: Tally) -> None:
    value = str(answer_of(reply).get("text") or "") if structured else plain(reply)
    want = norm(getattr(item.doc, item.field or ""))
    tally.counts["total"] += 1
    tally.counts["right"] += norm(value) == want
    tally.distance += distance(norm(value), want)


def score(profile: Profile, items: list[Item], parser: Parser) -> dict[str, Any]:
    done = replies(profile)
    tallies = {task: Tally() for task in ("sheets", "requirements", "cells")}
    ids = iter(range(10**7))
    structured = profile.prompt is None and profile.name != BASELINE
    for item in items:
        row = done.get((item.task, item.key))
        tally = tallies[item.task]
        tally.counts["items"] += 1
        if row is None or "error" in row:
            tally.errors += 1
            continue
        tally.seconds += row["sec"]
        tally.counts["read"] += 1
        if item.task == "sheets":
            score_sheet(item, row["reply"], structured, tally)
        elif item.task == "requirements":
            score_block(item, row["reply"], structured, parser, tally, ids)
        else:
            score_cell(item, row["reply"], structured, tally)
    return {
        "model": profile.name,
        "prompt": profile.prompt,
        "tasks": {
            task: {**t.counts, "seconds": round(t.seconds, 1), "unread": t.errors, "distance": round(t.distance, 3)}
            for task, t in tallies.items()
        },
    }


def share(a: int | None, b: int | None) -> str:
    return f"{a or 0} из {b}" if b else "—"


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Локальные мультимодальные модели на одних задачах",
        "",
        f"Получено `{result['command']}` {result['date']}; {result['machine']}; {result['tesseract']}.",
        "Модели загружались по одной на локальном сервере, полностью — [vlm-bench.json](vlm-bench.json).",
        "",
        f"Листы — {result['items']['sheets']} реальных сканов эталона, чаще всего не по ГОСТ 2.104: модель с "
        "JSON-схемой заполняет поля, OCR-модель выдаёт текст листа. ТТ и ячейки — синтетические растры и сканы, "
        "вырезанные по рамке разметки, поэтому поиск области не измерен и это верхняя граница. Значения сравниваются "
        "по правилу `eval/gold.py`. «В ответе» — значение целиком есть в тексте ответа, «выдумано» — непустое значение "
        "там, где на листе поля нет. Параметры ТТ — категории, которые пишутся только в блоке; часть их формулировок "
        "ядро не знает (пробелы этапа 5), поэтому 100 % здесь недостижимы ни для кого. Это сравнение моделей, а не "
        "итоговая метрика.",
        "",
        "| Модель | Листы: поля верно | в ответе | выдумано | с на лист | ТТ: параметры | диапазоны | с на блок "
        "| Ячейки: верно | CER | с на ячейку | Не прочитано |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result["models"]:
        s, b, c = (row["tasks"][task] for task in ("sheets", "requirements", "cells"))
        lost = sum(t["unread"] for t in row["tasks"].values())
        right = share(s.get("right"), s.get("total")) if row["model"] != BASELINE and not row["prompt"] else "—"
        invented = share(s.get("invented"), s.get("blank")) if right != "—" else "—"
        label = Profile(row["model"], row["prompt"]).label
        lines.append(
            f"| {label} | {right} | {share(s.get('in_reply'), s.get('total'))} | {invented} "
            f"| {s['seconds'] / max(s.get('read', 0), 1):.1f} "
            f"| {share(b.get('right'), b.get('total'))} | {share(b.get('ranges_right'), b.get('ranges'))} "
            f"| {b['seconds'] / max(b.get('read', 0), 1):.1f} "
            f"| {share(c.get('right'), c.get('total'))} "
            f"| {c['distance'] / max(c.get('total', 0), 1):.3f} "
            f"| {c['seconds'] / max(c.get('read', 0), 1):.1f} | {lost} |"
        )
    lines += ["", "Промпты:", ""]
    lines += [f"- `{name}` sha256:{digest}" for name, digest in result["prompts"].items()]
    return "\n".join(lines) + "\n"


def profiles_of(args: argparse.Namespace) -> list[Profile]:
    texts = [Profile(*item.split("=", 1)) for item in args.text_model]
    return [Profile(BASELINE), *(Profile(name) for name in args.model), *texts]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", action="append", default=[], help="модель с JSON-схемой, id на сервере")
    parser.add_argument("--text-model", action="append", default=[], help="OCR-модель: id=имя промпта")
    parser.add_argument("--url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--requirements", type=int, default=30, help="блоков ТТ")
    parser.add_argument("--cell-docs", type=int, default=20, help="документов для ячеек основной надписи")
    parser.add_argument("--budget", type=float, default=180, help="минут на модель")
    parser.add_argument("--score", action="store_true", help="только пересчитать сохранённые ответы")
    args = parser.parse_args(argv)
    settings = load_settings(use_ocr=True)
    labels = dict(settings.models.vlm.fields)
    items = corpus(args.requirements, args.cell_docs)
    profiles = profiles_of(args)
    if not args.score:
        for profile in profiles:
            run_model(profile, items, args.url, labels, args.budget)
    words = Parser(settings.dictionaries)
    prompts = {"title_block_cell.v1", "bench_sheet_fields.v1", "bench_requirements.v1"}
    prompts |= {p.prompt for p in profiles if p.prompt}
    version = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    result = {
        "command": "uv run python eval/vlm_bench.py " + " ".join(argv or []),
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "tesseract": version,
        "items": Counter(item.task for item in items),
        "prompts": {name: prompt_version(name) for name in sorted(prompts)},
        "models": [score(profile, items, words) for profile in profiles],
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "vlm-bench.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "vlm-bench.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
