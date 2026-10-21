"""Demo data for the frontend: the baseline pipeline on the committed demo kit, metrics against its ground truth,
rendered pages and report exports.

    python -m arm_web build-demo

Writes to ``frontend/public/demo``, so the demo report opens in the web UI without a server. The kit itself comes
from the generator: ``uv run python -m synth.demo_kit samples/demo_kit``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from kdcheck.render import RenderError, render_page
from kdcheck.report import to_html, to_xlsx
from kdcheck.run import BaselinePipeline, load_settings, ocr_available

from .evaluate import evaluate
from .settings import config_dir


def _files(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} файл"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} файла"
    return f"{n} файлов"


def build(samples: Path, public_demo: Path) -> None:
    kit = samples / "demo_kit"
    truth = json.loads((samples / "demo_kit_truth.json").read_text(encoding="utf-8"))

    settings = load_settings(config_dir())
    report = BaselinePipeline(settings).run(kit, kit_id="demo", kit_name="Демо-комплект: редуктор колёсный")
    report.kit.created_at = report.kit.processed_at
    n_specs = sum(1 for d in truth["documents"].values() if d["kind"] == "specification")
    n_draw = len(truth["documents"]) - n_specs
    n_scan = sum(1 for d in truth["documents"].values() if d.get("origin") == "scan")
    report.quality = evaluate(
        report,
        truth,
        dataset=(
            "Синтетический комплект «Редуктор колёсный»: "
            f"{_files(len(truth['documents']) + len(truth['problem_files']))} — "
            f"{n_specs} спецификации, {n_draw} чертежей (из них {n_scan} скана), "
            f"{len(truth['problem_files'])} проблемных файла; заложено {len(truth['discrepancies'])} расхождений. "
            f"OCR: {'Tesseract' if ocr_available(settings) else 'не используется'}."
        ),
    )

    pages_dir = public_demo / "pages"
    shutil.rmtree(pages_dir, ignore_errors=True)
    pages_dir.mkdir(parents=True)
    for doc in report.documents:
        for page in doc.pages:
            try:
                (pages_dir / f"{doc.id}-{page.number}.png").write_bytes(
                    render_page(kit / doc.path, page.number, max_px=1600, pdf_backend=settings.models.pdf.backend)
                )
            except RenderError as exc:
                print(f"  страница не отрисована: {doc.path} — {exc}")
    (public_demo / "report.json").write_text(report.model_dump_json(exclude_none=True), encoding="utf-8")
    (public_demo / "report.xlsx").write_bytes(to_xlsx(report))
    (public_demo / "report.html").write_text(to_html(report), encoding="utf-8")
    s = report.summary
    print(
        f"Демо-отчёт: {public_demo} — документов {s.documents_total}, расхождений {s.discrepancies_total}, "
        f"критичных параметров {s.critical_parameters}"
    )
