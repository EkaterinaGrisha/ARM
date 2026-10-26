"""Title blocks of scans with a vision-language model and without it (ADR-0014).

Runs the baseline pipeline with OCR on the demo kit and on synthetic kits of ``synth.kits`` built from a seed into
``data/synth/vlm_kits`` (outside git; clean kits, half of the drawings scanned). First without the model, then with
each model given, and compares the title block fields of the scanned drawings with the truth: right, wrong and
missing per field, and how many values the model gave and how many of them are right. ``--verify MODEL`` adds a run
where that model also reads the cells OCR filled; the conflicts are counted with the reading the truth supports.

The models run on an OpenAI-compatible server on this machine, LM Studio by default; the server must have them.
Writes eval/results/vlm-title-block.json and eval/results/vlm-title-block.md.

    uv run python eval/vlm_title_block.py --model google/gemma-4-e4b --model qwen/qwen3.6-27b \
        [--verify google/gemma-4-e4b]
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kdcheck.config import Settings
from kdcheck.pipeline import BaselinePipeline
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Report
from synth.truth import DrawingTruth, KitTruth

from synth import kits

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "samples" / "demo_kit"
CORPUS = ROOT / "data" / "synth" / "vlm_kits"
OUT = Path(__file__).resolve().parent / "results"
FIELDS = ("designation", "name", "material", "mass", "scale", "litera")
CONFLICT = re.compile(r"«(?P<label>[^»]+)»: прочитано «(?P<ocr>[^»]*)», модель прочла «(?P<vlm>[^»]*)»")


def norm(value: object) -> str:
    """Values compare after case, «ё», decimal comma and spaces: the rules of samples/demo_kit_truth.json."""
    if value is None:
        return ""
    text = str(value).lower().replace("ё", "е").replace(",", ".")
    return re.sub(r"\s+", "", text)


def settings_for(model: str | None, url: str, verify: bool) -> Settings:
    settings = load_settings(use_ocr=True, use_vlm=model is not None)
    if model is None:
        return settings
    vlm = settings.models.vlm.model_copy(update={"base_url": url, "model": model, "verify": verify})
    return settings.model_copy(update={"models": settings.models.model_copy(update={"vlm": vlm})})


def score(report: Report, truth: KitTruth, labels: dict[str, str], tally: dict[str, Counter[str]]) -> None:
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    notes = {d.id: d.notes for d in report.documents}
    by_label = {label: field for field, label in labels.items()}
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth) or doc.origin != "scan":
            continue
        tally["drawings"]["scans"] += 1
        part = parts.get(ids.get(path, ""))
        tb = part.title_block if part else None
        for field in FIELDS:
            want = getattr(doc, field)
            if want is None:
                continue
            got = getattr(tb, field) if tb else None
            counts = tally[field]
            counts["total"] += 1
            if got is None or got.value in (None, ""):
                counts["missing"] += 1
                continue
            right = norm(got.value) == norm(want)
            counts["right" if right else "wrong"] += 1
            if got.source and got.source.method == "vlm":
                counts["by_model"] += 1
                counts["by_model_right"] += right
        for note in notes.get(ids.get(path, ""), []):
            m = CONFLICT.search(note)
            if m and m["label"] in by_label:
                want = getattr(doc, by_label[m["label"]])
                tally["conflicts"]["total"] += 1
                tally["conflicts"]["model_right"] += norm(m["vlm"]) == norm(want)
                tally["conflicts"]["kept_right"] += norm(m["ocr"]) == norm(want)


def run(name: str, model: str | None, url: str, verify: bool, truths: dict[Path, KitTruth]) -> dict[str, Any]:
    settings = settings_for(model, url, verify)
    pipeline = BaselinePipeline(settings)
    tally: dict[str, Counter[str]] = {key: Counter() for key in (*FIELDS, "drawings", "conflicts")}
    started = time.monotonic()
    problems = []
    for folder, truth in truths.items():
        report = pipeline.run(folder, kit_id=folder.name, kit_name=folder.name)
        problems += [e.message for e in report.log if "Модель" in e.message]
        score(report, truth, dict(settings.models.vlm.fields), tally)
    seconds = round(time.monotonic() - started, 1)
    fields = {}
    for field in FIELDS:
        c = tally[field]
        fields[field] = {
            "total": c["total"],
            "right": c["right"],
            "wrong": c["wrong"],
            "missing": c["missing"],
            "accuracy": round(c["right"] / c["total"], 3) if c["total"] else None,
            "by_model": c["by_model"],
            "by_model_right": c["by_model_right"],
        }
    total = sum(f["total"] for f in fields.values())
    right = sum(f["right"] for f in fields.values())
    return {
        "name": name,
        "model": model,
        "prompt": pipeline.vlm.version if pipeline.vlm else None,
        "verify": verify,
        "scans": tally["drawings"]["scans"],
        "accuracy": round(right / total, 3) if total else None,
        "fields": fields,
        "model_values": sum(f["by_model"] for f in fields.values()),
        "model_values_right": sum(f["by_model_right"] for f in fields.values()),
        "model_calls": pipeline.vlm.calls if pipeline.vlm else 0,
        "conflicts": dict(tally["conflicts"]),
        "seconds": seconds,
        "problems": sorted(set(problems)),
    }


def corpus(count: int, seed: int) -> dict[Path, KitTruth]:
    shutil.rmtree(CORPUS, ignore_errors=True)
    CORPUS.mkdir(parents=True)
    built = kits.corpus(CORPUS, count, seed, clean_every=1, scan_share=0.5)
    truths = {DEMO: KitTruth.read(ROOT / "samples" / "demo_kit_truth.json")}
    truths.update({CORPUS / f"kit_{i:03d}": t for i, t in enumerate(built, start=1)})
    return truths


def markdown(result: dict[str, Any]) -> str:
    env = result["environment"]
    lines = [
        "# Основная надпись сканов: OCR и мультимодальная модель",
        "",
        f"Получено `uv run python eval/vlm_title_block.py {result['command']}` {result['date']}; "
        f"{env['machine']}, Python {env['python']}, Tesseract {env['tesseract']}; сервер моделей {result['url']}.",
        f"Корпус: демо-комплект и {result['kits']} синтетических комплектов (сид {result['seed']}, половина чертежей — "
        f"сканы), всего {result['runs'][0]['scans']} сканов чертежей; полностью — "
        "[vlm-title-block.json](vlm-title-block.json).",
        "Поля: обозначение, наименование, материал, масса, масштаб, литера; сравнение после регистра, «ё», "
        "десятичной запятой и пробелов. Модель читает только ячейки, которые OCR оставил пустыми"
        + (", а с проверкой — и заполненные" if any(r["verify"] for r in result["runs"]) else "")
        + ".",
        "",
        "| Прогон | Точность | Значений от модели | Из них верных | Обращений к модели | Конфликтов | Время, с |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in result["runs"]:
        conflicts = r["conflicts"].get("total", 0)
        lines.append(
            f"| {r['name']} | {r['accuracy']} | {r['model_values']} | {r['model_values_right']} | "
            f"{r['model_calls']} | {conflicts} | {r['seconds']} |"
        )
    lines += ["", "По полям — верно из всех, в скобках пропусков:", ""]
    lines.append("| Прогон | " + " | ".join(FIELDS) + " |")
    lines.append("| --- |" + " ---: |" * len(FIELDS))
    for r in result["runs"]:
        cells = [f"{f['right']}/{f['total']} ({f['missing']})" for f in r["fields"].values()]
        lines.append(f"| {r['name']} | " + " | ".join(cells) + " |")
    verify = [r for r in result["runs"] if r["verify"] and r["conflicts"].get("total")]
    if verify:
        lines += ["", "Конфликты при проверке: чьё прочтение совпало с эталоном.", ""]
        lines += ["| Прогон | Конфликтов | Модель права | OCR прав |", "| --- | ---: | ---: | ---: |"]
        for r in verify:
            c = r["conflicts"]
            lines.append(f"| {r['name']} | {c['total']} | {c.get('model_right', 0)} | {c.get('kept_right', 0)} |")
    problems = sorted({p for r in result["runs"] for p in r["problems"]})
    if problems:
        lines += ["", "Сообщения прогона:", "", *[f"- {p}" for p in problems]]
    lines += [
        "",
        "Сканы синтетические: шрифт и деградации свои (ADR-0013), реальные сканы АРМ могут дать другие цифры. "
        "Решение включать модель по умолчанию — только по замеру на эталонной выборке (этап 8).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--model", action="append", default=[], help="модель на сервере; можно несколько")
    parser.add_argument("--url", default="http://127.0.0.1:1234/v1", help="OpenAI-совместимый сервер моделей")
    parser.add_argument(
        "--verify",
        action="append",
        default=[],
        help="модель, которая в отдельном прогоне читает и ячейки, заполненные OCR",
    )
    parser.add_argument("--kits", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2)
    args = parser.parse_args(argv)
    if not ocr_available(load_settings(use_ocr=True)):
        parser.error("нужен Tesseract с языками rus+eng: замер сравнивает модель с OCR")
    truths = corpus(args.kits, args.seed)
    runs = [run("OCR", None, args.url, False, truths)]
    for model in args.model:
        runs.append(run(f"OCR + {model}", model, args.url, False, truths))
    for model in args.verify:
        runs.append(run(f"OCR + {model}, проверка", model, args.url, True, truths))
    tesseract = next(
        (e.version for e in BaselinePipeline(load_settings(use_ocr=True)).engines() if e.name == "tesseract"), None
    )
    result = {
        "command": " ".join([*(f"--model {m}" for m in args.model), *(f"--verify {m}" for m in args.verify)]),
        "date": datetime.now(UTC).date().isoformat(),
        "url": args.url,
        "kits": args.kits,
        "seed": args.seed,
        "environment": {
            "machine": f"{platform.system()} {platform.machine()}",
            "python": platform.python_version(),
            "tesseract": tesseract,
        },
        "runs": runs,
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "vlm-title-block.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "vlm-title-block.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
