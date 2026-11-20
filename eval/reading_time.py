"""Time of reading kits by the number of reading processes and of requests to the model at once (plan, item «г» of
19.11, ADR-0032).

Runs the core with OCR and without the read cache on targets of ``snapshots.py`` — ``arm``, the dev kits of the
customer, and ``synth``, the corpus of synth_kits.py — once per number, and checks that the report stays the same:
the numbers change the time of a run, never its report. Without the model each report is checked against its
snapshot; ``--threads`` reads in threads of the job process instead of processes, for comparison. ``--vlm`` turns the
model on and takes the numbers as requests at once (``vlm.requests_at_once``); the reports of the first number are
those the others are checked against, so the list starts with 1.

Writes eval/results/reading-time.json and .md.

    uv run python eval/reading_time.py arm synth --processes 1,2,4,6,8
    uv run python eval/reading_time.py arm --processes 4 --threads
    uv run python eval/reading_time.py arm --processes 1,4 --vlm
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import snapshots
from kdcheck import cache as read_cache
from kdcheck.ingest import kit as kit_reading
from kdcheck.ocr import tesseract
from kdcheck.run import BaselinePipeline, load_settings
from kdcheck.vlm.client import VlmClient, load_prompt

RESULTS = Path(__file__).resolve().parent / "results"


@contextmanager
def in_threads() -> Iterator[None]:
    """The pool of the core with threads of this process in place of processes."""

    def pool(count: int, mp_context: Any, initializer: Any, initargs: Any) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(count, initializer=initializer, initargs=initargs)

    before = kit_reading.ProcessPoolExecutor
    kit_reading.ProcessPoolExecutor = pool
    try:
        yield
    finally:
        kit_reading.ProcessPoolExecutor = before


def measure(target: str, count: int, reading: str, first: dict[str, Any]) -> dict[str, Any]:
    """One run of every kit of ``target``; ``first`` keeps the reports of the first run with the model by kit."""
    settings = load_settings(use_ocr=True, use_vlm=reading == "model")
    if reading == "model":
        vlm = settings.models.vlm.model_copy(update={"requests_at_once": count})
        settings = settings.model_copy(update={"models": settings.models.model_copy(update={"vlm": vlm})})
    else:
        settings = settings.model_copy(update={"ingest": settings.ingest.model_copy(update={"processes": count})})
    pipeline = BaselinePipeline(settings)
    kits = snapshots.kits(target)
    sheets = same = 0
    started = time.monotonic()
    with read_cache.use(None), in_threads() if reading == "threads" else nullcontext():
        for name, path in kits.items():
            report = snapshots.snapshot(pipeline.run(path, kit_id=name, kit_name=name))
            sheets += sum(len(d["pages"]) for d in report["documents"])
            if reading == "model":
                saved = first.setdefault(name, report)
            else:
                saved = json.loads((snapshots.folder(target) / f"{name}.json").read_text(encoding="utf-8"))
            same += not snapshots.changes(saved, report)
    seconds = round(time.monotonic() - started, 1)
    print(f"{target}: {reading} {count}, {seconds} с, отчёт тот же {same} из {len(kits)}")
    return {
        "target": target,
        "reading": reading,
        "count": count,
        "kits": len(kits),
        "sheets": sheets,
        "seconds": seconds,
        "same_as_snapshot": same,
    }


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Время чтения комплектов по числу процессов",
        "",
        f"Получено `uv run python eval/reading_time.py` {result['date']}; {result['machine']}, ядер {result['cpus']}, "
        f"tesseract {result['tesseract']}, модель {result['model']}. Ядро читает с OCR и без кэша прочтений. "
        "Полностью — в [reading-time.json](reading-time.json).",
        "",
        "Чтение: «процессы» и «потоки» — файлы комплекта читают столько процессов или потоков, модель выключена; "
        "«модель» — модель включена, файлы читают процессы по умолчанию, а число — запросы к модели сразу. Время — "
        "весь прогон ядра по комплектам цели. Ускорение — к числу 1 того же чтения: «потоки» — к одному процессу. "
        "«Отчёт тот же» — отчёты, совпавшие целиком, без времени обработки: без модели — со снимком "
        "[snapshots.py](../snapshots.py), с моделью — с отчётом при одном запросе сразу.",
        "",
        "| Цель | Комплектов | Листов | Чтение | Число | Время, с | с на лист | Ускорение | Отчёт тот же |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    names = {"processes": "процессы", "threads": "потоки", "model": "модель"}
    alone = {
        (r["target"], r["reading"] == "model"): r["seconds"]
        for r in result["runs"]
        if r["count"] == 1 and r["reading"] != "threads"
    }
    for r in result["runs"]:
        reading = names[r["reading"]]
        base = alone.get((r["target"], r["reading"] == "model"))
        speedup = f"{base / r['seconds']:.2f}" if base else "—"
        lines.append(
            f"| `{r['target']}` | {r['kits']} | {r['sheets']} | {reading} | {r['count']} | {r['seconds']} | "
            f"{r['seconds'] / max(1, r['sheets']):.2f} | {speedup} | {r['same_as_snapshot']} из {r['kits']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", choices=("arm", "synth"))
    parser.add_argument("--processes", default="1,2,4,6,8", help="numbers, comma-separated")
    parser.add_argument("--threads", action="store_true", help="threads of the job process in place of processes")
    parser.add_argument("--vlm", action="store_true", help="the model on, the numbers are requests at once")
    args = parser.parse_args()
    settings = load_settings(use_ocr=True, use_vlm=args.vlm)
    if not tesseract.available(settings.models.ocr.languages):
        raise SystemExit("Нет Tesseract: время чтения меряется с OCR")
    if args.vlm and (problem := VlmClient(settings.models.vlm, load_prompt(settings.models.vlm.prompt)).unavailable()):
        raise SystemExit(f"Модель недоступна: {problem}")
    reading = "model" if args.vlm else "threads" if args.threads else "processes"
    counts = [int(n) for n in args.processes.split(",")]
    first: dict[str, Any] = {}
    runs = [measure(target, n, reading, first) for target in args.targets for n in counts]
    out = RESULTS / "reading-time.json"
    kept = json.loads(out.read_text(encoding="utf-8"))["runs"] if out.exists() else []
    fresh = {(r["target"], r["reading"], r["count"]) for r in runs}
    runs = [r for r in kept if (r["target"], r["reading"], r["count"]) not in fresh] + runs
    runs.sort(key=lambda r: (r["target"], r["reading"], r["count"]))
    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "cpus": os.cpu_count(),
        "tesseract": tesseract.version(),
        "model": settings.models.vlm.model,
        "runs": runs,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (RESULTS / "reading-time.md").write_text(markdown(result), encoding="utf-8")


if __name__ == "__main__":
    main()
