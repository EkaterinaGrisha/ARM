"""Compare the PdfBackend implementations on the committed demo kit (ADR-0007).

For each backend, with and without OCR: extraction metrics against samples/demo_kit_truth.json and reading time.
Writes eval/results/pdf-backends.json and eval/results/pdf-backends.md.

    uv run python eval/pdf_backends.py
"""

from __future__ import annotations

import importlib.metadata as metadata
import json
import os
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from arm_web.evaluate import evaluate
from kdcheck.ingest.pdf import BACKENDS, default_backend
from kdcheck.pipeline import BaselinePipeline
from kdcheck.run import ocr_available

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "samples" / "demo_kit"
TRUTH = json.loads((ROOT / "samples" / "demo_kit_truth.json").read_text(encoding="utf-8"))
OUT = Path(__file__).resolve().parent / "results"
KEY_METRICS = [
    ("Расхождения — всего", "f1"),
    ("Основная надпись — векторные PDF", "accuracy"),
    ("Основная надпись — сканы", "accuracy"),
    ("Параметры технических требований", "f1"),
    ("Строки спецификаций (раздел, обозначение, наименование, кол.)", "accuracy"),
]


def page_times(name: str) -> dict[str, float]:
    """Median milliseconds per PDF page for the text layer and for a 200 dpi render."""
    backend = default_backend(name)
    text, render = [], []
    for path in sorted(KIT.rglob("*.pdf")):
        try:
            pdf = backend.open(path.read_bytes())
        except Exception:
            continue  # the damaged file of the kit
        for i in range(pdf.page_count):
            page = pdf.page(i)
            t = time.perf_counter()
            page.lines()
            text.append(time.perf_counter() - t)
            t = time.perf_counter()
            page.render(200 / 72)
            render.append(time.perf_counter() - t)
    ms = {"text_ms": text, "render_ms": render}
    return {key: round(statistics.median(values) * 1000, 1) for key, values in ms.items()}


def run(name: str, use_ocr: bool) -> dict[str, object]:
    os.environ["ARM_PDF_BACKEND"] = name
    started = time.perf_counter()
    report = BaselinePipeline(use_ocr=use_ocr).run(KIT, kit_id="bench", kit_name="bench")
    seconds = time.perf_counter() - started
    metrics = {}
    for m in evaluate(report, TRUTH).metrics:
        values = {k: getattr(m, k) for k in ("precision", "recall", "f1", "accuracy") if getattr(m, k) is not None}
        metrics[m.name] = {**values, "support": m.support}
    return {"backend": name, "ocr": use_ocr, "kit_seconds": round(seconds, 2), "metrics": metrics}


def versions() -> dict[str, str]:
    found = {}
    for package in ("pypdfium2", "pymupdf", "pytesseract"):
        try:
            found[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            pass
    try:
        out = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, check=True).stdout
        found["tesseract"] = out.split()[1]
    except (OSError, subprocess.CalledProcessError, IndexError):
        pass
    return found


def markdown(result: dict) -> str:
    columns = [f"{n} ({k})" for n, k in KEY_METRICS] + ["Комплект, с", "Текст, мс/лист", "Рендер, мс/лист"]
    rows = [
        "| Движок | OCR | " + " | ".join(columns) + " |",
        "| --- | --- | " + " | ".join("---" for _ in KEY_METRICS) + " | --- | --- | --- |",
    ]
    for r in result["runs"]:
        values = [r["metrics"].get(n, {}).get(k) for n, k in KEY_METRICS]
        times = result["page_times"][r["backend"]]
        rows.append(
            f"| {r['backend']} | {'да' if r['ocr'] else 'нет'} | "
            + " | ".join("—" if v is None else f"{v:g}" for v in values)
            + f" | {r['kit_seconds']:g} | {times['text_ms']:g} | {times['render_ms']:g} |"
        )
    env = ", ".join(f"{k} {v}" for k, v in result["versions"].items())
    return (
        "# Движки PDF на демо-комплекте\n\n"
        f"Получено `uv run python eval/pdf_backends.py` {result['date']}; {result['machine']}; {env}.\n"
        "Метрики — по `samples/demo_kit_truth.json`, полностью в [pdf-backends.json](pdf-backends.json).\n\n"
        + "\n".join(rows)
        + "\n"
    )


def main() -> None:
    use_ocr = [False, True] if ocr_available() else [False]
    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "versions": versions(),
        "kit": "samples/demo_kit",
        "page_times": {name: page_times(name) for name in BACKENDS},
        "runs": [run(name, ocr) for name in BACKENDS for ocr in use_ocr],
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "pdf-backends.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "pdf-backends.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))


if __name__ == "__main__":
    main()
