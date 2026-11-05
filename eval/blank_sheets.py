"""Blank sheets (plan item 4.1): which pages the core takes for blank paper, on synthetic and real sheets.

The pages go through the loader of the core with OCR off (``kdcheck.ingest.document.load_document``), so the check
measured is the one of a run: a page without text whose image holds nothing but paper is blank.

1. Synthetic blank sheets: an empty page of A4 and A3 through every class of synth.degrade (raster; scan good,
   fair, poor, unreadable) and of Augraphy (scan good, fair, poor) when its group is installed, six seeds each. Every
   one should be blank.
2. Synthetic drawings: every page of the corpus of eval/scan_quality.py (``data/synth/scan_kits``: vector pages,
   rasters and scans of each class, drawings scanned below what a person reads) and of the stress corpus through
   Augraphy (``data/synth/stress``) when it is there. None should be blank.
3. Real sheets: the scans of the gold set (``data/gold_sheets``), drawings all, none should be blank; the open corpus
   (``data/open_drawings``), the first pages of each file, with the pages taken for blank listed by file and page for
   review; the ARM kits of the roles given (``data/arm/kits.json``), counts only. The test kits are measured once,
   with the rules frozen.

Writes eval/results/blank-sheets.json and eval/results/blank-sheets.md.

    uv run python eval/blank_sheets.py [--pages 40] [--arm dev test]
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from kdcheck.config import Settings
from kdcheck.ingest.document import load_document
from kdcheck.run import load_settings
from synth.model import Output

from synth import degrade

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCAN_KITS = DATA / "synth" / "scan_kits"
STRESS = DATA / "synth" / "stress"
SHEETS = DATA / "gold_sheets"
OPEN = DATA / "open_drawings"
ARM = DATA / "arm"
OUT = Path(__file__).resolve().parent / "results"
SIZES = {"A4": (210, 297), "A3": (420, 297)}  # mm; the generator draws an A2 or A1 scan at 600 dpi past its noise
CLASSES = {"raster": ("good",), "scan": ("good", "fair", "poor", "unreadable")}
AUGRAPHY = ("good", "fair", "poor")
SEEDS = 6
EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def _settings(pages: int) -> Settings:
    settings = load_settings(use_ocr=False)
    ingest = settings.ingest.model_copy(update={"max_pages_per_file": pages})
    return settings.model_copy(update={"ingest": ingest})


def _blank_pdf(size: tuple[float, float]) -> bytes:
    doc = pdfium.PdfDocument.new()
    doc.new_page(size[0] * 72 / 25.4, size[1] * 72 / 25.4)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pages(folder: Path, root: Path, settings: Settings) -> list[dict[str, Any]]:
    """Every page of the files under ``folder``: whether the loader took it for blank, and the file status."""
    rows = []
    for path in sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTENSIONS and p.is_file()):
        doc = load_document(path, root, settings)
        for page in doc.pages:
            rows.append(
                {"file": doc.info.path, "page": page.number, "blank": page.blank, "text": bool(page.lines)}
                | {"status": doc.info.status}
            )
    return rows


def synthetic_blanks(folder: Path, settings: Settings) -> dict[str, Any]:
    """Empty pages through each class of capture: how many of them the loader takes for blank, by class."""
    try:
        from synth import augraphy_scans
    except ImportError:  # the group installs on Python 3.11 only: uv run --group augraphy
        augraphy_scans = None
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for size, mm in SIZES.items():
        pdf = _blank_pdf(mm)
        for kind, qualities in CLASSES.items():
            for quality in qualities:
                for seed in range(SEEDS):
                    data, _ = degrade.capture(pdf, Output(kind, "jpg", quality), seed)
                    made.append((f"{kind} {quality}", folder / f"{size}_{kind}_{quality}_{seed}.jpg", data))
        for quality in AUGRAPHY if augraphy_scans else ():
            for seed in range(SEEDS):
                data, _ = augraphy_scans.capture(pdf, Output("scan", "jpg", quality), seed)
                made.append((f"Augraphy {quality}", folder / f"{size}_augraphy_{quality}_{seed}.jpg", data))
    by_class: dict[str, Counter[str]] = {}
    for label, path, data in made:
        path.write_bytes(data)
        doc = load_document(path, folder, settings)
        by_class.setdefault(label, Counter()).update(total=1, blank=doc.info.status == "blank")
    return {"augraphy": augraphy_scans is not None, "by_class": {k: dict(v) for k, v in by_class.items()}}


def _count(rows: list[dict[str, Any]]) -> dict[str, Any]:
    images = [r for r in rows if not r["text"]]
    return {
        "files": len({r["file"] for r in rows}),
        "pages": len(rows),
        "without_text": len(images),
        "blank_pages": sum(r["blank"] for r in rows),
        "blank_files": len({r["file"] for r in rows if r["status"] == "blank"}),
    }


def arm_kits(role: str, settings: Settings) -> dict[str, Any]:
    """The ARM kits of ``role``: counts only, the kits are the customer's."""
    kits = json.loads((ARM / "kits.json").read_text(encoding="utf-8"))
    return {
        kit: _count(_pages(ARM / info["path"], ARM / info["path"], settings))
        for kit, info in sorted(kits.items())
        if info["role"] == role
    }


CAPTURE = {"raster": "растр", "scan": "скан", "Augraphy": "скан Augraphy"}
QUALITY = {"good": "хорошее", "fair": "среднее", "poor": "плохое", "unreadable": "нечитаемое"}


def _label(key: str) -> str:
    kind, quality = key.split()
    name = CAPTURE[kind]
    return f"{name[0].upper()}{name[1:]}, {QUALITY[quality]}"


def markdown(result: dict[str, Any]) -> str:
    env, rules, roles = result["environment"], result["rules"], " ".join(result["arm_roles"])
    lines = [
        "# Пустые листы",
        "",
        f"Получено `uv run python eval/blank_sheets.py --pages {result['pages']} --arm {roles}` "
        f"{result['date']}; {env['machine']}, Python {env['python']}, {result['seconds']} с. Пустым загрузчик ядра "
        "считает лист без текста, у которого на копии "
        f"{rules['scale']:g} пкс/мм без полос по {rules['margin']:g} мм у краёв отметки есть не больше чем в "
        f"{rules['max_cells']} клетках по {rules['cell']:g} мм. Отметка — деталь уже {rules['detail']} пкс копии, "
        f"на {rules['min_darkness']:g} уровней серого темнее бумаги вокруг; клетка с отметками — от "
        f"{rules['cell_marks']:.0%} её площади. Страница меньше {rules['min_cells']} клеток — не лист: заглушка или "
        "картинка, она меряется целиком (`blank` в configs/ingest.yaml); "
        f"полностью — [blank-sheets.json](blank-sheets.json).",
        "",
        "## Синтетические пустые листы",
        "",
        f"Пустая страница A4 и A3 через каждый класс съёмки генератора, по {SEEDS} сидов. Пустыми должны быть все.",
        "",
        "| Съёмка | Пустых | Всего |",
        "| --- | ---: | ---: |",
    ]
    blanks = result["synthetic_blanks"]
    for label, c in blanks["by_class"].items():
        lines.append(f"| {_label(label)} | {c.get('blank', 0)} | {c['total']} |")
    if not blanks["augraphy"]:
        lines += ["", "Augraphy не установлен (`uv run --group augraphy`): его классы не измерены."]
    lines += [
        "",
        "## Чертежи и реальные листы",
        "",
        "Пустыми не должен быть ни один лист синтетики, эталона и комплектов АРМ. Открытый корпус и комплекты — первые "
        f"{result['pages']} страниц каждого файла; листы, принятые за пустые, перечислены ниже для просмотра.",
        "",
        "| Корпус | Файлов | Страниц | Из них без текста | Пустых страниц | Пустых файлов |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, c in result["corpora"].items():
        lines.append(
            f"| {name} | {c['files']} | {c['pages']} | {c['without_text']} | {c['blank_pages']} | {c['blank_files']} |"
        )
    if result["open_blank"]:
        lines += ["", "Открытый корпус, страницы, принятые за пустые:", ""]
        by_file: dict[str, list[int]] = {}
        for r in result["open_blank"]:
            by_file.setdefault(r["file"], []).append(r["page"])
        lines += [f"- {f}: {', '.join(map(str, pages))}" for f, pages in by_file.items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--pages", type=int, default=40, help="страниц на файл открытого корпуса и комплектов АРМ")
    parser.add_argument(
        "--arm", nargs="*", default=["dev"], choices=("dev", "test"), help="роли комплектов АРМ; без ролей — без них"
    )
    args = parser.parse_args(argv)
    settings = _settings(args.pages)
    started = time.monotonic()
    blanks = synthetic_blanks(DATA / "synth" / "blank_sheets", settings)
    corpora: dict[str, dict[str, Any]] = {}
    open_rows: list[dict[str, Any]] = []
    for name, folder in (("Синтетика: сканы и растры", SCAN_KITS), ("Синтетика: Augraphy", STRESS)):
        if folder.is_dir():
            corpora[name] = _count(_pages(folder, folder, settings))
    if SHEETS.is_dir():
        corpora["Эталон: сканы"] = _count(_pages(SHEETS, SHEETS, settings))
    if OPEN.is_dir():
        open_rows = _pages(OPEN, OPEN, settings)
        corpora["Открытый корпус"] = _count(open_rows)
    if (ARM / "kits.json").is_file():
        for role in args.arm:
            corpora |= {f"АРМ {kit} ({role})": c for kit, c in arm_kits(role, settings).items()}
    result: dict[str, Any] = {
        "date": datetime.now(UTC).date().isoformat(),
        "pages": args.pages,
        "arm_roles": args.arm,
        "environment": {"machine": f"{platform.system()} {platform.machine()}", "python": platform.python_version()},
        "rules": settings.ingest.blank.model_dump(),
        "synthetic_blanks": blanks,
        "corpora": corpora,
        "open_blank": [{"file": r["file"], "page": r["page"]} for r in open_rows if r["blank"]],
    }
    result["seconds"] = round(time.monotonic() - started, 1)
    OUT.mkdir(exist_ok=True)
    (OUT / "blank-sheets.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "blank-sheets.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
