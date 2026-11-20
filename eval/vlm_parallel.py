"""Requests to the model at once: time and answers against requests one by one (plan, item «г» of 19.11, ADR-0032).

``record`` runs the core with the model on a dev kit of the customer without the read cache and keeps its first
requests to the server with their answers in data/vlm_parallel/<kit>.json, outside git: the requests hold images of
the sheets. ``replay`` sends them again, one by one or several at once, and counts the answers that match the
recorded ones. How many requests the server runs at once is its own setting (LM Studio: ``lms load --parallel N``),
so ``--server`` names it in the results. Record with the server as the core uses it.

Writes eval/results/vlm-parallel.json and .md.

    uv run python eval/vlm_parallel.py record arm-1 --requests 60
    uv run python eval/vlm_parallel.py replay arm-1 --threads 1,4 --server "LM Studio, parallel 1"
"""

from __future__ import annotations

import argparse
import json
import platform
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import arm_kits
from kdcheck import cache as read_cache
from kdcheck.run import BaselinePipeline, load_settings
from kdcheck.vlm.client import VlmClient

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "vlm_parallel"
RESULTS = Path(__file__).resolve().parent / "results"


class Enough(BaseException):
    """Stops the run of the core once the requests are recorded; the core catches only Exception."""


def record(kit: str, count: int) -> None:
    entry = arm_kits.load(arm_kits.KITS)[kit]
    if entry["role"] != "dev":
        raise SystemExit(f"{kit}: роль {entry['role']}, а запросы записываются только с комплектов dev")
    settings = load_settings(use_ocr=True, use_vlm=True)
    records: list[dict[str, Any]] = []
    answer = VlmClient._answer

    def kept(client: VlmClient, body: dict[str, Any]) -> str:
        started = time.monotonic()
        text = answer(client, body)
        records.append({"body": body, "answer": text, "seconds": round(time.monotonic() - started, 2)})
        if len(records) >= count:
            raise Enough
        return text

    VlmClient._answer = kept
    try:
        with read_cache.use(None):
            BaselinePipeline(settings).run(arm_kits.DATA / entry["path"], kit_id=kit, kit_name=kit)
    except Enough:
        pass
    finally:
        VlmClient._answer = answer
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / f"{kit}.json").write_text(json.dumps(records), encoding="utf-8")
    print(f"{kit}: записано запросов {len(records)}")


def _ask(url: str, body: dict[str, Any]) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=300) as response:
        message = json.loads(response.read())["choices"][0]["message"]
    text: str = json.loads(message.get("content") or message.get("reasoning_content"))["text"]
    return text.strip()


def replay(kit: str, threads: int, server: str) -> dict[str, Any]:
    records = json.loads((DATA / f"{kit}.json").read_text(encoding="utf-8"))
    url = load_settings().models.vlm.base_url.rstrip("/") + "/chat/completions"
    started = time.monotonic()
    with ThreadPoolExecutor(threads) as pool:
        answers = list(pool.map(lambda r: _ask(url, r["body"]), records))
    seconds = round(time.monotonic() - started, 1)
    same = sum(a == r["answer"] for a, r in zip(answers, records, strict=True))
    print(f"{server}: запросов сразу {threads}, {seconds} с, ответов как записано {same} из {len(records)}")
    return {
        "kit": kit,
        "server": server,
        "threads": threads,
        "requests": len(records),
        "seconds": seconds,
        "same": same,
    }


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Запросы к модели одновременно",
        "",
        f"Получено `uv run python eval/vlm_parallel.py` {result['date']}; {result['machine']}, "
        f"модель {result['model']}. "
        "Первые запросы ядра к модели на комплекте записаны прогоном с сервером, как его использует ядро, и посланы "
        "снова по одному или по нескольку сразу. «Как записано» — ответы, совпавшие с записанными посимвольно. "
        "Полностью — в [vlm-parallel.json](vlm-parallel.json).",
        "",
        "| Комплект | Сервер | Запросов сразу | Запросов | Время, с | с на запрос | Как записано |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in result["runs"]:
        lines.append(
            f"| `{r['kit']}` | {r['server']} | {r['threads']} | {r['requests']} | {r['seconds']} | "
            f"{r['seconds'] / max(1, r['requests']):.2f} | {r['same']} из {r['requests']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record")
    rec.add_argument("kit")
    rec.add_argument("--requests", type=int, default=60)
    rep = commands.add_parser("replay")
    rep.add_argument("kit")
    rep.add_argument("--threads", default="1,4", help="requests at once, comma-separated")
    rep.add_argument("--server", required=True, help="the server and how many requests it runs at once")
    args = parser.parse_args()
    if args.command == "record":
        record(args.kit, args.requests)
        return
    runs = [replay(args.kit, int(n), args.server) for n in args.threads.split(",")]
    out = RESULTS / "vlm-parallel.json"
    kept = json.loads(out.read_text(encoding="utf-8"))["runs"] if out.exists() else []
    fresh = {(r["kit"], r["server"], r["threads"]) for r in runs}
    runs = [r for r in kept if (r["kit"], r["server"], r["threads"]) not in fresh] + runs
    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "model": load_settings().models.vlm.model,
        "runs": runs,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (RESULTS / "vlm-parallel.md").write_text(markdown(result), encoding="utf-8")


if __name__ == "__main__":
    main()
