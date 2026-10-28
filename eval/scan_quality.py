"""Rasters and scans of synth.degrade against real scans, and what each class costs the reading of the core (ADR-0013).

Two measurements.

1. Realism. The same image statistics of the real scans of the gold set (eval/gold/sheets.csv, cut into
   data/gold_sheets by ``eval/gold.py sheets``) and of the synthetic rasters and scans of each class: resolution and
   encoding, the levels of ink and paper, noise of the paper, stroke width, specks, unevenness of the paper, skew. The
   skew of a synthetic scan is known, so the error of its estimate is measured too.
2. Reading by class. The baseline pipeline with OCR on clean synthetic kits built from a seed into
   ``data/synth/scan_kits`` (outside git), most of their documents captured: the title block fields of drawings and
   of specifications, and the designations of specification rows, by input (vector, raster, scan) and class.

Writes eval/results/scan-quality.json and eval/results/scan-quality.md.

    uv run python eval/scan_quality.py [--kits 6] [--seed 3]
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import shutil
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from kdcheck.pipeline import BaselinePipeline
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Report
from PIL import Image, ImageChops, ImageFilter, ImageStat
from synth.degrade import CLASSES
from synth.model import Output
from synth.render import draw_drawing
from synth.truth import DrawingTruth, KitTruth, SpecTruth
from vlm_title_block import FIELDS, norm, sheet_inputs

from synth import degrade, kits

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "synth" / "scan_kits"
SHEETS = ROOT / "data" / "gold_sheets"
GOLD = ROOT / "eval" / "gold"
OUT = Path(__file__).resolve().parent / "results"
SCAN_SHARE, RASTER_SHARE = 0.45, 0.3
UNREADABLE = 6  # drawings captured below what a person reads, for the table of statistics
STATS = ("dpi", "ink", "paper", "noise", "stroke", "specks", "uneven", "skew")


# ---------------------------------------------------------------------------------------- image statistics


def page_image(path: Path) -> tuple[Image.Image, float, str]:
    """The first page in grey at the pixels of its file, pixels per millimetre, and how the file keeps them."""
    if path.suffix.lower() == ".pdf":
        page = pdfium.PdfDocument(path)[0]
        images = list(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]))
        biggest = max(images, key=lambda o: o.get_px_size()[0] * o.get_px_size()[1])
        ppmm = biggest.get_px_size()[0] / (page.get_size()[0] / 72 * 25.4)
        bits = biggest.get_metadata().bits_per_pixel
        encoding = f"PDF {'+'.join(biggest.get_filters())}, {bits} бит"
        return page.render(scale=ppmm * 25.4 / 72, grayscale=True).to_pil().convert("L"), ppmm, encoding
    image = Image.open(path)
    dpi = float(image.info.get("dpi", (300, 300))[0])
    return image.convert("L"), dpi / 25.4, f"{image.format} {image.mode}"


def _otsu(hist: list[int]) -> int:
    """The grey level that splits the histogram into ink and paper best (Otsu)."""
    total, weighted = sum(hist), sum(i * h for i, h in enumerate(hist))
    best, level, below, below_sum = -1.0, 128, 0, 0.0
    for t, count in enumerate(hist):
        below += count
        below_sum += t * count
        if not below or below == total:
            continue
        between = below * (total - below) * (below_sum / below - (weighted - below_sum) / (total - below)) ** 2
        if between > best:
            best, level = between, t
    return level


def _quantile(hist: list[int], q: float, low: int = 0, high: int = 255) -> int:
    part = hist[low : high + 1]
    goal, seen = q * sum(part), 0
    for i, count in enumerate(part):
        seen += count
        if seen >= goal:
            return low + i
    return high


def _uneven(page: Image.Image, ppmm: float) -> float:
    """How the paper level varies over the sheet: the deviation of its 90th percentile in cells of 10 mm."""
    cell = max(4, round(10 * ppmm))
    levels = [
        _quantile(page.crop((x, y, x + cell, y + cell)).histogram(), 0.9)
        for y in range(0, page.height - cell + 1, cell)
        for x in range(0, page.width - cell + 1, cell)
    ]
    return statistics.pstdev(levels)


def _skew(page: Image.Image, threshold: int, ppmm: float) -> float:
    """The turn of the sheet in degrees, counter clockwise: the angle that lines up the rows and columns of ink."""
    ink = page.point(lambda v: 255 if v <= threshold else 0)
    small = ink.resize((round(page.width / ppmm * 4), round(page.height / ppmm * 4)), Image.Resampling.BOX)

    def lined_up(angle: float) -> float:
        # nearest pixels: an interpolating turn blurs the ink and would favour the angle that turns nothing
        turned = small.rotate(angle, resample=Image.Resampling.NEAREST)
        rows = turned.resize((1, turned.height), Image.Resampling.BOX)
        cols = turned.resize((turned.width, 1), Image.Resampling.BOX)
        return ImageStat.Stat(rows).var[0] + ImageStat.Stat(cols).var[0]

    best = max((a / 4 for a in range(-16, 17)), key=lined_up)
    best = max((best + a / 40 for a in range(-10, 11)), key=lined_up)
    return -best + 0.0  # no «-0.0»


def image_statistics(image: Image.Image, ppmm: float) -> dict[str, float]:
    """Statistics of a scanned page, by the same rules for real and synthetic ones.

    Ink and paper are the median levels on either side of the Otsu threshold. Noise is the deviation of the paper
    from its 3×3 median, away from ink. Stroke width is twice the ink area over its boundary. Specks are ink with at
    most six ink pixels in its 7×7 neighbourhood, in mm² per dm². These four are measured on the quarter of the sheet
    with the title block, to bound the time; unevenness of the paper and skew on the whole sheet.
    """
    margin = round(0.03 * min(image.size))
    page = image.crop((margin, margin, image.width - margin, image.height - margin))
    hist = page.histogram()
    threshold = _otsu(hist)
    quarter = page.crop((page.width // 2, page.height // 2, page.width, page.height))
    ink = quarter.point(lambda v: 255 if v <= threshold else 0)
    area = ink.histogram()[255]
    inner = ink.filter(ImageFilter.MinFilter(3)).histogram()[255]
    residual = ImageChops.subtract(quarter, quarter.filter(ImageFilter.MedianFilter(3)), 1.0, 128)
    paper = ImageChops.invert(ink.filter(ImageFilter.MaxFilter(5)))
    lonely = ink.filter(ImageFilter.BoxBlur(3)).point(lambda v: 255 if v <= 6 * 255 / 49 else 0)
    specks = ImageChops.multiply(ink, lonely).histogram()[255]
    dm2 = quarter.width * quarter.height / ppmm**2 / 10_000
    return {
        "dpi": round(ppmm * 25.4),
        "ink": _quantile(hist, 0.5, 0, threshold),
        "paper": _quantile(hist, 0.5, threshold + 1, 255),
        "noise": round(ImageStat.Stat(residual, paper).stddev[0], 1),
        "stroke": round(2 * area / max(1, area - inner) / ppmm, 2),
        "specks": round(specks / ppmm**2 / dm2, 1),
        "uneven": round(_uneven(page, ppmm), 1),
        "skew": round(_skew(page, threshold, ppmm), 2),
    }


def real_sheets() -> list[dict[str, Any]]:
    """The scans of the gold set with their statistics; none when the sheets are not cut yet."""
    if not SHEETS.is_dir():
        return []
    with (GOLD / "sheets.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    with (ROOT / "eval" / "open_drawings.csv").open(encoding="utf-8", newline="") as f:
        titles = {row["path"]: row["title"] for row in csv.DictReader(f)}
    inputs = sheet_inputs()
    gold = KitTruth.read(GOLD / "sheets.json").documents
    measured = []
    for row in rows:
        path = SHEETS / row["sheet"]
        truth = gold.get(row["sheet"])
        if not path.exists() or truth is None or truth.origin == "vector":
            continue
        image, ppmm, encoding = page_image(path)
        measured.append(
            {
                "sheet": row["sheet"],
                "group": f"{titles.get(row['source'], row['source'])}: {inputs[row['sheet']]}",
                "encoding": encoding,
                **image_statistics(image, ppmm),
            }
        )
    return measured


def synthetic_sheets(truths: dict[Path, KitTruth], unreadable: list[Path]) -> list[dict[str, Any]]:
    """The captured documents of the corpus and the unreadable scans, with their statistics and known skew."""
    measured = []
    for folder, truth in truths.items():
        for path, doc in truth.documents.items():
            if doc.capture is None:
                continue
            image, ppmm, encoding = page_image(folder / path)
            sheet = doc.capture.sheets[0] if doc.capture.sheets else None
            measured.append(
                {
                    "sheet": f"{folder.name}/{path}",
                    "group": f"{doc.capture.kind}:{doc.capture.quality}",
                    "mode": doc.capture.mode,
                    "encoding": encoding,
                    "true_skew": sheet.skew if sheet else 0.0,
                    **image_statistics(image, ppmm),
                }
            )
    for path in unreadable:
        image, ppmm, encoding = page_image(path)
        measured.append({"sheet": path.name, "group": "scan:unreadable", "mode": "grey", "encoding": encoding})
        measured[-1] |= image_statistics(image, ppmm)
    return measured


def summary(measured: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Median and range of each statistic by group."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in measured:
        groups[m["group"]].append(m)
    result = {}
    for group, items in groups.items():
        result[group] = {
            "sheets": len(items),
            "encodings": dict(Counter(m["encoding"] for m in items).most_common()),
            **{
                s: {
                    "median": statistics.median(m[s] for m in items),
                    "min": min(m[s] for m in items),
                    "max": max(m[s] for m in items),
                }
                for s in STATS
            },
        }
        errors = [abs(m["skew"] - m["true_skew"]) for m in items if "true_skew" in m]
        if errors:
            result[group]["skew_error"] = round(statistics.mean(errors), 2)
    return result


# ---------------------------------------------------------------------------------------- reading by class


def _key(doc: DrawingTruth | SpecTruth) -> str:
    return "vector" if doc.capture is None else f"{doc.capture.kind}:{doc.capture.quality}"


def score(report: Report, truth: KitTruth, tally: dict[str, Counter[str]]) -> None:
    """Right values by input and class; for the title blocks of drawings also by resolution and by pixels."""
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    specs = {s.document_id: s for s in report.specifications}
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth | SpecTruth):
            continue
        t = tally[_key(doc)]
        doc_id = ids.get(path, "")
        if isinstance(doc, DrawingTruth):
            slices = [t]
            if doc.capture is not None:
                slices += [tally[f"dpi:{doc.capture.dpi}"], tally[f"mode:{doc.capture.mode}"]]
            tb = parts[doc_id].title_block if doc_id in parts else None
            for counts in slices:
                counts["drawings"] += 1
            for field in FIELDS:
                want = getattr(doc, field)
                if want in (None, ""):
                    continue
                got = getattr(tb, field) if tb else None
                right = got is not None and norm(got.value) == norm(want)
                for counts in slices:
                    counts["drawing_fields"] += 1
                    counts["drawing_right"] += right
            continue
        t["specs"] += 1
        spec = specs.get(doc_id)
        for field in ("designation", "name"):
            got = getattr(spec, field) if spec else None
            t["spec_fields"] += 1
            t["spec_right"] += got is not None and norm(got.value) == norm(getattr(doc, field))
        found = {norm(r.designation.value) for r in spec.rows if r.designation} if spec else set()
        for row in doc.rows:
            if row.designation:
                t["rows"] += 1
                t["rows_found"] += norm(row.designation) in found


def read_by_class(truths: dict[Path, KitTruth]) -> dict[str, Any]:
    pipeline = BaselinePipeline(load_settings(use_ocr=True))
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    started = time.monotonic()
    for folder, truth in truths.items():
        score(pipeline.run(folder, kit_id=folder.name, kit_name=folder.name), truth, tally)
    return {"seconds": round(time.monotonic() - started, 1), "by_class": {k: dict(v) for k, v in tally.items()}}


# ---------------------------------------------------------------------------------------- corpus and output


def corpus(count: int, seed: int) -> tuple[dict[Path, KitTruth], list[Path]]:
    """Clean kits with most documents captured, and a few drawings scanned below what a person reads."""
    shutil.rmtree(CORPUS, ignore_errors=True)
    CORPUS.mkdir(parents=True)
    built = kits.corpus(CORPUS, count, seed, clean_every=1, scan_share=SCAN_SHARE, raster_share=RASTER_SHARE)
    truths = {CORPUS / f"kit_{i:03d}": t for i, t in enumerate(built, start=1)}
    folder = CORPUS / "unreadable"
    folder.mkdir()
    kit = kits.build(seed, [], scan_share=0, raster_share=0)
    unreadable = []
    for i, drawing in enumerate([d for d in kit.drawings if d.sheets == 1][:UNREADABLE]):
        data, _ = degrade.capture(draw_drawing(drawing), Output("scan", "jpg", "unreadable"), seed * 100 + i)
        unreadable.append(folder / f"{i:02d}.jpg")
        unreadable[-1].write_bytes(data)
    return truths, unreadable


def _tesseract() -> str | None:
    try:
        out = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, check=True).stdout
        return out.split()[1]
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None


def _range(cell: dict[str, Any]) -> str:
    def show(v: float) -> str:
        return f"{v:g}".replace(".", ",")

    if cell["min"] == cell["max"]:
        return show(cell["median"])
    return f"{show(cell['median'])} ({show(cell['min'])}…{show(cell['max'])})"


def _order(key: str) -> tuple[int, str]:
    value = key.split(":", 1)[1]
    return (int(value), "") if value.isdigit() else (["bilevel", "grey", "colour"].index(value), value)


def _share(right: int, total: int) -> str:
    return f"{right}/{total} ({right / total:.2f})".replace(".", ",") if total else "—"


GROUPS = {
    "raster:good": "растр, хорошее",
    "raster:fair": "растр, среднее",
    "raster:poor": "растр, плохое",
    "scan:good": "скан, хорошее",
    "scan:fair": "скан, среднее",
    "scan:poor": "скан, плохое",
    "scan:unreadable": "скан, нечитаемое",
    "vector": "вектор",
}
PARAMETERS = (
    ("dpi", "Разрешение, dpi"),
    ("modes", "Пиксели: ч/б, серые, цвет"),
    ("jpeg", "Качество JPEG"),
    ("ink", "Тушь, уровень серого"),
    ("paper", "Бумага, уровень серого"),
    ("spread", "Утолщение штриха на сторону, мм"),
    ("fade", "Выцветание туши в пятнах, доля"),
    ("stains", "Пятна бумаги, уровней серого"),
    ("stamp", "Штамп на листе, вероятность"),
    ("fold", "Сгиб листа больше A4, вероятность"),
    ("skew", "Наклон, до °"),
    ("shift", "Сдвиг, до мм"),
    ("shadow", "Тень у края, вероятность"),
    ("dust", "Пыль, точек на 100 см²"),
    ("blur", "Размытие σ, мм"),
    ("noise", "Шум σ, уровней серого"),
    ("dark_lid", "Тёмная крышка сканера, вероятность"),
    ("whiten", "Удаление фона драйвером, вероятность"),
)


def _parameter(field: str, value: Any) -> str:
    """A cell of the table of classes: shares of the pixel modes, a set of resolutions, a range or a number."""
    if field == "modes":
        counts = Counter(value)
        return " : ".join(str(counts[m]) for m in ("bilevel", "grey", "colour"))
    if field == "dpi":
        return ", ".join(str(v) for v in value)
    if isinstance(value, tuple) and value[0] != value[1]:
        return f"{value[0]:g}…{value[1]:g}".replace(".", ",")
    return f"{value[0] if isinstance(value, tuple) else value:g}".replace(".", ",")


def markdown(result: dict[str, Any]) -> str:
    env = result["environment"]
    names = ("good", "fair", "poor", "unreadable")
    lines = [
        "# Деградации «как скан»: реализм и чтение по классам качества",
        "",
        f"Получено `uv run python eval/scan_quality.py --kits {result['kits']} --seed {result['seed']}` "
        f"{result['date']}; {env['machine']}, Python {env['python']}, Tesseract {env['tesseract']}. "
        "Полностью — [scan-quality.json](scan-quality.json).",
        "",
        "## Классы качества",
        "",
        "Каждый параметр растра или скана берётся из диапазона класса по сиду документа и пишется в разметку "
        "(`synth.degrade.CLASSES`, `synth.truth.Capture`). Хорошее, среднее и плохое читаются человеком, как кейс "
        "обещает о своих сканах; нечитаемое — ниже этой границы, для проблемного файла мутации `unreadable_scan`. "
        "Растр без сканирования берёт из класса только разрешение и сжатие.",
        "",
        "| Параметр | " + " | ".join(GROUPS[f"scan:{q}"].removeprefix("скан, ") for q in names) + " |",
        "| --- |" + " --- |" * len(names),
    ]
    for field, title in PARAMETERS:
        lines.append(f"| {title} | " + " | ".join(_parameter(field, getattr(CLASSES[q], field)) for q in names) + " |")
    lines += [
        "",
        "## Реальные и синтетические сканы",
        "",
        "Одни и те же статистики изображения для сканов эталона (data/gold_sheets: альбом ВЛ60 1963 года, фото "
        "ИЖ-250, альбомы приборов) и для растров и сканов корпуса. Тушь и бумага — медианы уровней серого по обе "
        "стороны порога Оцу; шум — отклонение бумаги от медианы 3×3 вдали от туши; штрих — удвоенная площадь туши "
        "на её границу; крап — одиночные точки туши, мм² на дм²; неровность — разброс уровня бумаги по клеткам 10 мм; "
        "наклон — по профилю строк и столбцов туши. Медиана, в скобках — разброс.",
        "",
        "| Набор | Листов | Кодирование | dpi | Тушь | Бумага | Шум σ | Штрих, мм | Крап, мм²/дм² | Неровность | "
        "Наклон, ° |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    stats = result["statistics"]
    for group in sorted(g for g in stats if g not in GROUPS) + [g for g in GROUPS if g in stats]:
        s = stats[group]
        encodings = "; ".join(f"{k} — {v}" for k, v in s["encodings"].items())
        lines.append(
            f"| {GROUPS.get(group, group)} | {s['sheets']} | {encodings} | "
            + " | ".join(_range(s[k]) for k in STATS)
            + " |"
        )
    errors = {g: s["skew_error"] for g, s in result["statistics"].items() if "skew_error" in s}
    if errors:
        mean = f"{statistics.mean(errors.values()):.2f}".replace(".", ",")
        each = ", ".join(f"{GROUPS[g]} — {errors[g]:g}°".replace(".", ",") for g in GROUPS if g in errors)
        lines += ["", f"Наклон синтетического скана известен: средняя ошибка оценки — {mean}° ({each})."]
    reading = result["reading"]
    seconds = f"{reading['seconds']:g}".replace(".", ",")
    lines += [
        "",
        "## Чтение ядром по виду входа и классу",
        "",
        f"Ядро с OCR на {result['kits']} чистых синтетических комплектах; доля сканов в генераторе — "
        f"{round(SCAN_SHARE * 100)} %, растров — {round(RASTER_SHARE * 100)} %, классы поровну. Поля основной "
        "надписи чертежа — обозначение, наименование, материал, масса, масштаб, литера; спецификации — обозначение и "
        "наименование. Строки — "
        "обозначения строк спецификации, найденные в разобранной спецификации. Сравнение после регистра, «ё», "
        f"десятичной запятой и пробелов. Время прогона — {seconds} с.",
        "",
        "| Вход | Чертежей | Основная надпись чертежа | Спецификаций | Основная надпись спецификации | Строки |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in GROUPS:
        t = reading["by_class"].get(group)
        if not t:
            continue
        drawings = _share(t.get("drawing_right", 0), t.get("drawing_fields", 0))
        specs = _share(t.get("spec_right", 0), t.get("spec_fields", 0))
        rows = _share(t.get("rows_found", 0), t.get("rows", 0))
        count = f"{t.get('drawings', 0)} | {drawings} | {t.get('specs', 0)}"
        lines.append(f"| {GROUPS[group]} | {count} | {specs} | {rows} |")
    for prefix, title, names in (
        ("dpi", "Разрешение, dpi", lambda k: k),
        ("mode", "Пиксели", lambda k: {"bilevel": "ч/б", "grey": "серые", "colour": "цвет"}[k]),
    ):
        keys = sorted((k for k in reading["by_class"] if k.startswith(f"{prefix}:")), key=_order)
        lines += ["", f"Основная надпись чертежей-растров и сканов по признаку «{title}»:", ""]
        lines += [f"| {title} | Чертежей | Верно |", "| --- | ---: | ---: |"]
        for key in keys:
            t = reading["by_class"][key]
            share = _share(t.get("drawing_right", 0), t.get("drawing_fields", 0))
            lines.append(f"| {names(key.split(':', 1)[1])} | {t.get('drawings', 0)} | {share} |")
    lines += [
        "",
        "Реальных сканов мало, и все они старые: статистики показывают, попадают ли классы в их диапазон, но не "
        "калибруют классы. Калибровка — по сканам АРМ, когда они будут (ADR-0013).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--kits", type=int, default=6)
    parser.add_argument("--seed", type=int, default=3)
    args = parser.parse_args(argv)
    if not ocr_available():
        raise SystemExit("Нужен Tesseract с русским языком: замер читает сканы через OCR")
    truths, unreadable = corpus(args.kits, args.seed)
    measured = real_sheets() + synthetic_sheets(truths, unreadable)
    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "kits": args.kits,
        "seed": args.seed,
        "environment": {
            "machine": f"{platform.system()} {platform.machine()}",
            "python": platform.python_version(),
            "tesseract": _tesseract(),
        },
        "statistics": summary(measured),
        "reading": read_by_class(truths),
        "sheets": measured,
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "scan-quality.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "scan-quality.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
