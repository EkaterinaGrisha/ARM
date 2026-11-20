"""Time of reading kits by the number of reading processes (plan, item «г» of 19.11, ADR-0032).

Runs the core with OCR and without the read cache on targets of ``snapshots.py`` — ``arm``, the dev kits of the
customer, and ``synth``, the corpus of synth_kits.py — once per number of processes, and checks each report against
its snapshot: the processes change the time of a run, never its report. ``--threads`` reads in threads of the job
process instead of processes, for comparison. The model is off: its time depends on the server, not on the core.

Writes eval/results/reading-time.json and .md.

    uv run python eval/reading_time.py arm synth --processes 1,2,4,6,8
    uv run python eval/reading_time.py arm --processes 4 --threads
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


def measure(target: str, count: int, threads: bool) -> dict[str, Any]:
    settings = load_settings(use_ocr=True, use_vlm=False)
    settings = settings.model_copy(update={"ingest": settings.ingest.model_copy(update={"processes": count})})
    pipeline = BaselinePipeline(settings)
    kits = snapshots.kits(target)
    sheets = same = 0
    started = time.monotonic()
    with read_cache.use(None), in_threads() if threads else nullcontext():
        for name, path in kits.items():
            report = pipeline.run(path, kit_id=name, kit_name=name)
            sheets += sum(len(d.pages) for d in report.documents)
            saved = json.loads((snapshots.folder(target) / f"{name}.json").read_text(encoding="utf-8"))
            same += not snapshots.changes(saved, snapshots.snapshot(report))
    seconds = round(time.monotonic() - started, 1)
    print(f"{target}: {'потоков' if threads else 'процессов'} {count}, {seconds} с, как снимок {same} из {len(kits)}")
    return {
        "target": target,
        "reading": "threads" if threads else "processes",
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
        f"tesseract {result['tesseract']}. Ядро читает с OCR, без модели и без кэша прочтений; каждый отчёт сверяется "
        "со снимком [snapshots.py](../snapshots.py). Полностью — в [reading-time.json](reading-time.json).",
        "",
        "Время — весь прогон ядра по комплектам цели. Ускорение — к одному процессу той же цели. «Как снимок» — "
        "отчёты, которые совпали со снимком целиком, без времени обработки.",
        "",
        "| Цель | Комплектов | Листов | Чтение | Число | Время, с | с на лист | Ускорение | Как снимок |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    alone = {r["target"]: r["seconds"] for r in result["runs"] if r["count"] == 1}
    for r in result["runs"]:
        reading = "потоки" if r["reading"] == "threads" else "процессы"
        speedup = f"{alone[r['target']] / r['seconds']:.2f}" if r["target"] in alone else "—"
        lines.append(
            f"| `{r['target']}` | {r['kits']} | {r['sheets']} | {reading} | {r['count']} | {r['seconds']} | "
            f"{r['seconds'] / max(1, r['sheets']):.2f} | {speedup} | {r['same_as_snapshot']} из {r['kits']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", choices=("arm", "synth"))
    parser.add_argument("--processes", default="1,2,4,6,8", help="numbers of processes or threads, comma-separated")
    parser.add_argument("--threads", action="store_true", help="threads of the job process in place of processes")
    args = parser.parse_args()
    settings = load_settings(use_ocr=True)
    if not tesseract.available(settings.models.ocr.languages):
        raise SystemExit("Нет Tesseract: время чтения меряется с OCR")
    counts = [int(n) for n in args.processes.split(",")]
    runs = [measure(target, n, args.threads) for target in args.targets for n in counts]
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
        "runs": runs,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (RESULTS / "reading-time.md").write_text(markdown(result), encoding="utf-8")


if __name__ == "__main__":
    main()
