"""The drawing field of rasters and scans: which way of reading finds its parameters (plan, step 3; audit П1, О5).

The parameters of a drawing lie in two places: the block of technical requirements above the title block and the field
of the drawing, where dimensions with their tolerances, roughness signs and frames of form tolerances are drawn. The
metric of ``synth_kits.py`` mixes them; here the field is measured on its own. A parameter of the truth is of the field
when its box is no item of the technical requirements; a parameter of the report is of the field when the centre of its
region lies in no such item.

The corpus is clean kits of :mod:`synth.kits`, most documents captured as rasters and scans of three classes, as for
``scan_quality.py``, built from a seed into ``data/synth/field_kits`` (outside git). Each reader of the field runs the
core with OCR and replaces only how the lines of the field are read:

- ``tesseract``: the lines Tesseract finds on the whole page, as the core reads them now;
- ``tesseract-raw``: the same without the rules of GOST 2789 that give a roughness read by OCR its decimal sign back;
- ``ppocr-lines``: the lines Tesseract finds, each read again by PP-OCRv5 from its cut of the image (ADR-0024), which
  knows «Ø», «⊥», «∥», «√» and «±» and keeps small signs Tesseract drops;
- ``vlm-tiles``: the vision-language model of ``configs/models.yaml`` reads the sheet in four overlapping fragments
  with the prompt ``drawing_field.v1`` and writes each record with a parameter of accuracy; a record takes the region
  of the line of OCR its text is like or lies in, and is dropped when that line lies in the title block or the
  technical requirements; with no such line its region is the fragment;
- ``ppocr+vlm``: the lines of ``ppocr-lines``, and the records of the model. A parameter of a category its line of OCR
  already gave is a second reading of that line: when it reads otherwise, it takes the place of the parameter of OCR
  and keeps its region, as the model does in a cell of the title block (ADR-0019), and counts as a conflict;
- ``dots-page`` and ``ppocr+dots``: the same with the document model dots.mocr, which reads the whole sheet with the
  prompt ``bench_ocr_dots.v1``, as in the comparison of models of 02.11.2026; it is loaded in LM Studio for the run.

A fragment the server fails on is counted and left out; its drawing keeps what the other fragments gave. A model
reader is scored twice: with every record, and with the records like no line of OCR left out, whose region is a
fragment, a quarter of the sheet.

For each parameter of the field the truth gives, the run says whether it is found, whether its value and its region are
right, and, when it is missed, whether a line of the reader lies over its box at all: a missed line is a matter of
finding the text, a misread one of recognising it.

Writes eval/results/drawing-field.json and eval/results/drawing-field.md.

    uv run python eval/drawing_field.py [--reader tesseract] [--kits 6] [--seed 3]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import shutil
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import scoring
import vlm_bench
from kdcheck.config import Settings
from kdcheck.ingest.document import LoadedDocument, Page, page_raster
from kdcheck.ingest.text import Line
from kdcheck.ocr import ppocr
from kdcheck.pipeline import BaselinePipeline
from kdcheck.report.labels import PARAMETER_CATEGORY
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Parameter, Report
from kdcheck.vlm.client import VlmClient, VlmError, load_prompt
from PIL import Image
from synth.truth import DrawingTruth, KitTruth, ParameterTruth

from synth import kits

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "synth" / "field_kits"
RESULTS = ROOT / "eval" / "results"
SCAN_SHARE, RASTER_SHARE = 0.45, 0.3  # of scan_quality.py: most documents captured
FIELD_CATEGORIES = ("dimension_tolerance", "roughness", "geometric")  # what the field of a part drawing holds
FIELD_PROMPT = "drawing_field.v1"
DOTS, DOTS_PROMPT = "dots.mocr", vlm_bench.prompt_text("bench_ocr_dots.v1")
MODEL_READERS = ("vlm-tiles", "ppocr+vlm", "dots-page", "ppocr+dots")
TILE_PIXELS = 1400  # px: the long side of a fragment as the model gets it; with the answer it fits a context of 4096
LINE_MATCH = 0.5  # a record of the model this alike to a line of OCR takes its region


def _tiles(width: float, height: float, overlap: float = 0.1) -> list[BBox]:
    """Four fragments of a page, two by two, each overlapping the next by ``overlap`` of the page."""
    xs = [(0.0, width * (0.5 + overlap / 2)), (width * (0.5 - overlap / 2), width)]
    ys = [(0.0, height * (0.5 + overlap / 2)), (height * (0.5 - overlap / 2), height)]
    return [(x0, y0, x1, y1) for y0, y1 in ys for x0, x1 in xs]


BBox = tuple[float, float, float, float]


def _centre(bbox: BBox) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def _inside(point: tuple[float, float], bbox: BBox, pad: float = 0.005) -> bool:
    return bbox[0] - pad <= point[0] <= bbox[2] + pad and bbox[1] - pad <= point[1] <= bbox[3] + pad


def _overlap(a: BBox, b: BBox) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _alike(record: str, text: str) -> tuple[bool, float]:
    """Whether a record of the model lies in a line of OCR, and how alike the two are."""
    a, b = scoring.norm(record), scoring.norm(text)
    return bool(a) and a in b, SequenceMatcher(None, a, b).ratio()


def _ids_by_line(have: list[Parameter], pg: Page, lines: list[Line]) -> dict[int, dict[str, dict[str, str]]]:
    """The parameters each line of the page gave, by category, as normalised name → id; the line is the one their
    region is."""
    found: dict[int, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    boxes = [pg.norm_bbox(ln.bbox) for ln in lines]
    for p in have:
        if p.source is None or p.source.page != pg.number or p.source.bbox is None:
            continue
        centre = _centre(p.source.bbox)
        index = next((i for i, box in enumerate(boxes) if _inside(centre, box, pad=0)), None)
        if index is not None:
            found[index][p.category][scoring.norm(p.name)] = p.id
    return found


class FieldPipeline(BaselinePipeline):
    """The core with a reader of the field of its own; it keeps the lines the reader gave, by file and page."""

    def __init__(self, settings: Settings, reader: str) -> None:
        super().__init__(settings)
        self.reader = reader
        self.lines: dict[str, list[tuple[int, BBox, str]]] = defaultdict(list)
        self._raster: tuple[tuple[str, int], Image.Image, tuple[float, float], float] | None = None
        self._client: VlmClient | None = None
        self.failures: Counter[str] = Counter()  # fragments the server failed on, by why
        self.conflicts: Counter[str] = Counter()  # second readings of a line that read otherwise
        self.unlocated: set[str] = set()  # parameters of records like no line of OCR, by id

    def _annotations(
        self, doc: LoadedDocument, aside: Callable[[Page, Line], bool], next_id: Callable[[], str]
    ) -> list[Parameter]:
        for pg in doc.pages:
            if self.reader in ("ppocr-lines", "ppocr+vlm", "ppocr+dots") and pg.method == "ocr":
                pg.lines = [ln if ln.vertical or aside(pg, ln) else self._ppocr(doc, pg, ln) for ln in pg.lines]
            for ln in pg.lines:
                if not ln.vertical and not aside(pg, ln):
                    self.lines[doc.info.path].append((pg.number, pg.norm_bbox(ln.bbox), ln.text))
        found = super()._annotations(doc, aside, next_id)
        if self.reader not in MODEL_READERS:
            return found
        replaced: set[str] = set()
        if self.reader in ("vlm-tiles", "dots-page"):
            found = [
                p
                for p in found
                if p.source is None or p.source.page not in {pg.number for pg in doc.pages if pg.method == "ocr"}
            ]
        for pg in doc.pages:
            if pg.method == "ocr":
                added, by_model = self._model(doc, pg, aside, next_id, found)
                found += added
                replaced |= by_model
        return [p for p in found if p.id not in replaced]

    def _model(
        self,
        doc: LoadedDocument,
        pg: Page,
        aside: Callable[[Page, Line], bool],
        next_id: Callable[[], str],
        have: list[Parameter],
    ) -> tuple[list[Parameter], set[str]]:
        """The parameters of the records the model reads on the page, but those in ``have``, and the ids of those in
        ``have`` a second reading of their line replaces; the parameters of records like no line go to ``unlocated``.
        """
        dots = self.reader in ("dots-page", "ppocr+dots")
        if self._client is None and not dots:
            vlm = self.settings.models.vlm
            self._client = VlmClient(vlm.model_copy(update={"max_tokens": 1024}), load_prompt(FIELD_PROMPT))
            if problem := self._client.unavailable():
                raise SystemExit(f"Модель {vlm.model} недоступна: {problem}")
        image, (ox, oy), scale = page_raster(doc, pg, self.settings, self.pdf)
        sheet = self.settings.forms.sheet
        top, right = sheet.general_roughness_zone
        known = {
            (p.category, scoring.norm(p.name)) for p in have if p.source is not None and p.source.page == pg.number
        }
        lines = [ln for ln in pg.lines if not ln.vertical]
        read = _ids_by_line(have, pg, lines)
        modelled: dict[int, set[str]] = defaultdict(set)  # line → categories the model gave on it
        found: list[Parameter] = []
        replaced: set[str] = set()
        rects = [(0.0, 0.0, pg.width, pg.height)] if dots else _tiles(pg.width, pg.height)
        for rect in rects:
            crop = image.crop(tuple((v - o) / scale for v, o in zip(rect, (ox, oy, ox, oy), strict=True)))
            try:
                if dots:
                    url = self.settings.models.vlm.base_url
                    answer = vlm_bench.ask(url, DOTS, DOTS_PROMPT, vlm_bench.fit(crop), None)
                else:
                    assert self._client is not None
                    crop.thumbnail((TILE_PIXELS, TILE_PIXELS))
                    answer = self._client.read(crop, "поле чертежа")
            except (VlmError, OSError, ValueError, KeyError) as exc:  # one fragment lost, not the drawing
                self.failures[f"{type(exc).__name__}: {exc}"[:120]] += 1
                continue
            for record in (r.strip(" |#*-\t") for r in answer.splitlines()):
                if not record:
                    continue
                near = [i for i, ln in enumerate(lines) if _overlap(ln.bbox, rect)]
                alike, index = max(((_alike(record, lines[i].text), i) for i in near), default=((False, 0.0), -1))
                located = index >= 0 and (alike[0] or alike[1] >= LINE_MATCH)
                if located and aside(pg, lines[index]):
                    continue  # the title block or the technical requirements
                bbox = lines[index].bbox if located else rect
                line = Line(text=record, bbox=bbox, size=lines[index].size if index >= 0 else 3.5 * pg.mm)
                cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
                general = cy < top * pg.mm and cx > pg.width - right * pg.mm
                for param in self.parameters.annotation(doc, pg, line, next_id, general_zone=general):
                    key = (param.category, scoring.norm(param.name))
                    if located:
                        if param.category in modelled[index]:
                            continue  # read already in an overlapping fragment
                        modelled[index].add(param.category)
                        ocr = read[index][param.category]
                        if ocr and (key[1] in ocr or len(ocr) > 1):
                            continue  # read alike, or several of the category on the line to tell apart
                        if ocr:
                            [(name, ocr_id)] = ocr.items()
                            category = PARAMETER_CATEGORY.get(param.category, param.category)
                            self.conflicts[f"{category}: {name} ≠ {param.name}"] += 1
                            replaced.add(ocr_id)
                    elif key in known:
                        continue
                    known.add(key)
                    found.append(param)
                    if not located:
                        self.unlocated.add(param.id)
        return found, replaced

    def _ppocr(self, doc: LoadedDocument, pg: Page, ln: Line) -> Line:
        """The line read again by PP-OCRv5 from the image, with a margin of a quarter of its height."""
        reader = ppocr.recognizer(self.settings.models.ocr.ppocr)
        if reader is None:
            raise SystemExit("Нет PP-OCRv5: scripts/fetch_ppocr.sh")
        if self._raster is None or self._raster[0] != (doc.info.path, pg.number):
            image, origin, scale = page_raster(doc, pg, self.settings, self.pdf)
            self._raster = ((doc.info.path, pg.number), image.convert("L"), origin, scale)
        _, grey, (ox, oy), scale = self._raster
        x0, y0, x1, y1 = ln.bbox
        m = (y1 - y0) / 4
        crop = grey.crop(((x0 - m - ox) / scale, (y0 - m - oy) / scale, (x1 + m - ox) / scale, (y1 + m - oy) / scale))
        chars = ppocr.decode(reader.probabilities(crop, self.settings.models.ocr.ppocr.height), reader.alphabet)
        text = "".join(c.text for c in chars).strip()
        return dataclasses.replace(ln, text=text or ln.text, words=[])


def field_of(doc: DrawingTruth) -> list[ParameterTruth]:
    """The parameters of the truth drawn on the field: those whose box is no item of the technical requirements."""
    items = {(b.page, b.bbox) for b in doc.requirement_boxes if b is not None}
    return [p for p in doc.parameters if p.box is None or (p.box.page, p.box.bbox) not in items]


def in_requirements(p: Parameter, doc: DrawingTruth) -> bool:
    """Whether a parameter of the report comes from an item of the technical requirements, by its region."""
    if p.source is None or p.source.bbox is None:
        return False
    centre = _centre(p.source.bbox)
    return any(b is not None and b.page == p.source.page and _inside(centre, b.bbox) for b in doc.requirement_boxes)


def score(
    report: Report,
    truth: KitTruth,
    pipeline: FieldPipeline,
    tally: dict[str, Counter[str]],
    misses: Counter[str],
    extras: Counter[str],
) -> None:
    """The parameters of the field of each captured drawing against the truth, by «<input>:<category>» and by class
    of quality; ``misses`` tells why a parameter is missed."""
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth) or doc.origin == "vector" or doc.kind != "part_drawing":
            continue
        part = parts.get(ids.get(path, ""))
        got = [p for p in (part.parameters if part else []) if not in_requirements(p, doc)]
        wanted = [p for p in field_of(doc) if p.category in FIELD_CATEGORIES]
        quality = doc.capture.quality if doc.capture is not None else "—"
        others = [(b.page, b.bbox) for b in (*[w.box for w in wanted], *doc.requirement_boxes) if b is not None]
        lines = pipeline.lines.get(path, [])
        matches = scoring.match_parameters(wanted, got)
        for want, index in zip(wanted, matches, strict=True):
            for key in (f"{doc.origin}:{want.category}", f"{doc.origin}:всего", f"класс {quality}:всего"):
                counts = tally[key]
                counts["expected"] += 1
                if index is None:
                    continue
                counts["found"] += 1
                if scoring.has_value(want):
                    counts["valued"] += 1
                    counts["value_right"] += scoring.value_right(want, got[index])
                if want.box is not None:
                    source = got[index].source
                    region = (source.page, source.bbox) if source is not None and source.bbox is not None else None
                    counts["region_right"] += scoring.region_right((want.box.page, want.box.bbox), region, others)
            if index is None:
                box = want.box
                over = box is not None and any(pg == box.page and _overlap(bb, box.bbox) for pg, bb, _ in lines)
                why = "строка прочитана, параметр не разобран" if over else "строка не найдена"
                misses[f"{PARAMETER_CATEGORY.get(want.category, want.category)}: {why}"] += 1
        for i, found in enumerate(got):
            if i not in matches and found.category in FIELD_CATEGORIES:
                extras[f"{PARAMETER_CATEGORY.get(found.category, found.category)}: {found.name} ← «{found.text}»"] += 1
                tally[f"{doc.origin}:{found.category}"]["extra"] += 1
                tally[f"{doc.origin}:всего"]["extra"] += 1
                tally[f"класс {quality}:всего"]["extra"] += 1


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def summary(counts: Counter[str]) -> dict[str, Any]:
    found, expected, extra = counts["found"], counts["expected"], counts["extra"]
    precision, recall = _ratio(found, found + extra), _ratio(found, expected)
    f1 = round(2 * precision * recall / (precision + recall), 3) if precision and recall else 0.0
    return {
        **dict(counts),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "value_share": _ratio(counts["value_right"], counts["valued"]),
        "region_share": _ratio(counts["region_right"], found),
    }


def build_corpus(count: int, seed: int) -> dict[str, KitTruth]:
    if CORPUS.exists():
        shutil.rmtree(CORPUS)
    CORPUS.mkdir(parents=True)
    truths = kits.corpus(CORPUS, count, seed, clean_every=1, scan_share=SCAN_SHARE, raster_share=RASTER_SHARE)
    return {f"kit_{i:03d}": t for i, t in enumerate(truths, start=1)}


def run(reader: str, count: int, seed: int) -> dict[str, Any]:
    truths = build_corpus(count, seed)
    settings = load_settings(use_ocr=True)
    if reader == "tesseract-raw":
        words = settings.dictionaries
        raw = words.roughness.model_copy(update={"methods": ()})
        settings = settings.model_copy(update={"dictionaries": words.model_copy(update={"roughness": raw})})
    pipeline = FieldPipeline(settings, reader)
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    misses: Counter[str] = Counter()
    extras: Counter[str] = Counter()
    located: tuple[dict[str, Counter[str]], Counter[str], Counter[str]] = (defaultdict(Counter), Counter(), Counter())
    started, sheets, unlocated = time.monotonic(), 0, 0
    for kit, truth in truths.items():
        pipeline.lines.clear()
        pipeline.unlocated.clear()
        report = pipeline.run(CORPUS / kit, kit_id=kit, kit_name=kit)
        sheets += sum(len(d.pages) for d in report.documents)
        score(report, truth, pipeline, tally, misses, extras)
        if reader in MODEL_READERS:
            unlocated += len(pipeline.unlocated)
            score(_without(report, pipeline.unlocated), truth, pipeline, *located)
    model = {
        "unlocated": unlocated,
        "conflicts": dict(pipeline.conflicts.most_common(40)),
        "located_only": {
            "results": {key: summary(counts) for key, counts in sorted(located[0].items())},
            "misses": dict(located[1].most_common()),
        },
    }
    return {
        "reader": reader,
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "corpus": {"kits": count, "seed": seed, "scan_share": SCAN_SHARE, "raster_share": RASTER_SHARE},
        "engines": [f"{e.name} {e.version}" for e in pipeline.engines()],
        "seconds": round(time.monotonic() - started, 1),
        "sheets": sheets,
        "results": {key: summary(counts) for key, counts in sorted(tally.items())},
        "misses": dict(misses.most_common()),
        "extras": dict(extras.most_common(40)),
        "failed_fragments": dict(pipeline.failures),
        **(model if reader in MODEL_READERS else {}),
    }


def _without(report: Report, ids: set[str]) -> Report:
    """The report with the parameters of ``ids`` left out."""
    parts = [p.model_copy(update={"parameters": [x for x in p.parameters if x.id not in ids]}) for p in report.parts]
    return report.model_copy(update={"parts": parts})


def markdown(runs: dict[str, dict[str, Any]]) -> str:
    any_run = next(iter(runs.values()))
    corpus = any_run["corpus"]
    dates = ", ".join(sorted({run["date"] for run in runs.values()}))
    lines = [
        "# Поле чертежа растров и сканов",
        "",
        f"Получено `uv run python eval/drawing_field.py` {dates}; {any_run['machine']}. Корпус: "
        f"{corpus['kits']} чистых комплектов `synth.kits`, сид {corpus['seed']}, сканов {corpus['scan_share']:.0%}, "
        f"растров {corpus['raster_share']:.0%}. Полностью — в [drawing-field.json](drawing-field.json).",
        "",
        "Параметр поля — допуск размера, шероховатость или допуск формы и расположения, записанный на поле чертежа "
        "детали, а не в пункте технических требований. Найден, если в отчёте есть параметр той же категории с тем же "
        "именем (правило [scoring.py](../scoring.py)); область верна, если она указывает на значение и ни на какое "
        "другое. Запись модели получает область строки OCR, на которую похожа; запись без такой строки — область "
        "фрагмента, четверть листа. Строки «только со строкой OCR» считают способ без таких записей.",
        "",
        "| Способ чтения | Вход | Заложено | Найдено | Лишних | Точность | Полнота | F1 | Область верна | с на лист |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for reader, result in runs.items():
        per_sheet = round(result["seconds"] / result["sheets"], 2)
        variants = [(reader, result["results"])]
        if "located_only" in result:
            variants.append((f"{reader}, только со строкой OCR", result["located_only"]["results"]))
        for name, results in variants:
            for key in ("raster:всего", "scan:всего"):
                row = results.get(key)
                if row is None:
                    continue
                what = "растр" if key.startswith("raster") else "скан"
                lines.append(
                    f"| {name} | {what} | {row['expected']} | {row.get('found', 0)} | {row.get('extra', 0)} | "
                    f"{row['precision']} | {row['recall']} | {row['f1']} | {row['region_share']} | {per_sheet} |"
                )
    for reader, result in runs.items():
        lines += ["", f"## {reader}: по категориям и классам", "", "| Где | Заложено | Найдено | Лишних | F1 |"]
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for key, row in result["results"].items():
            where, _, category = key.partition(":")
            name = PARAMETER_CATEGORY.get(category, category)
            found, extra = row.get("found", 0), row.get("extra", 0)
            lines.append(f"| {where}, {name} | {row['expected']} | {found} | {extra} | {row['f1']} |")
        lines += ["", "| Почему пропущен | Параметров |", "| --- | ---: |"]
        lines += [f"| {why} | {n} |" for why, n in result["misses"].items()]
        if "conflicts" in result:
            lines += [
                "",
                f"Параметров из записей без строки OCR: {result['unlocated']}. Вторых прочтений строки, прочитанных "
                f"иначе: {sum(result['conflicts'].values())}.",
            ]
            if result["conflicts"]:
                lines += ["", "| OCR ≠ модель | Раз |", "| --- | ---: |"]
                lines += [f"| {pair} | {n} |" for pair, n in list(result["conflicts"].items())[:15]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--reader", default="tesseract", choices=("tesseract", "tesseract-raw", "ppocr-lines", *MODEL_READERS)
    )
    parser.add_argument("--kits", type=int, default=6)
    parser.add_argument("--seed", type=int, default=3)
    args = parser.parse_args(argv)
    if not ocr_available():
        print("Нет Tesseract: поле растров без OCR не читается")
        return 2
    path = RESULTS / "drawing-field.json"
    runs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    runs[args.reader] = run(args.reader, args.kits, args.seed)
    path.write_text(json.dumps(runs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "drawing-field.md").write_text(markdown(runs), encoding="utf-8")
    print(markdown(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
