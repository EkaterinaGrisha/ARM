"""Reading errors of the core on a frozen synthetic corpus: every value of the truth, read right or not, and the kind
of each error.

The corpus holds the clean kits of ``eval/scan_quality.py``, with its seed, count and shares of rasters and scans,
built once into ``data/synth/reading`` (outside git) and frozen by its manifest eval/reading_corpus.json: algorithms
against the errors are chosen on it, and their gain is measured on the same files. The stress corpus of Augraphy
(``--stress``) is measured only, never tuned on.

A record is a value of the truth: a title block field of a drawing or a specification, an item of technical
requirements, the designation, name or quantity of a specification row. It holds the document, the sheet, the field,
the region of the truth, the value of the truth and the one read, the engine, the confidence and the status of the
report, and the kind of error, none for a right value. A value is right by the rule of ``scoring``; a right value
whose region is not the box of the truth (``scoring.region_problem``) is an error of region. :func:`kind` tells the
other kinds apart. Items of technical requirements and rows the report gives beyond the truth are records too.

Writes eval/results/reading-errors[-vlm].json and .md, the errors in the common format to
eval/results/reading-errors[-vlm].csv, and every record to data/reading/records[-vlm].csv.

    uv run python eval/reading_errors.py build
    uv run python eval/reading_errors.py run [--vlm] [--cache] [--stress]
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import importlib.metadata
import json
import platform
import re
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import scoring
from kdcheck import cache as read_cache
from kdcheck.pipeline import BaselinePipeline
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Extracted, Report, SpecRow
from synth.heldout import LIBRARIES, commit_of_generator
from synth.stress import Manifest as StressManifest
from synth.stress import digests, source_digest
from synth.stress import verify as verify_stress
from synth.truth import Box, DrawingTruth, KitTruth, RowTruth, SpecTruth

from synth import kits

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "synth" / "reading"
MANIFEST = ROOT / "eval" / "reading_corpus.json"
STRESS = ROOT / "data" / "synth" / "stress"
STRESS_MANIFEST = ROOT / "eval" / "stress_corpus.json"
RECORDS = ROOT / "data" / "reading"
CACHE = ROOT / "data" / "cache"
OUT = Path(__file__).resolve().parent / "results"
PARAMETERS = {"kits": 6, "seed": 3, "clean_every": 1, "scan_share": 0.45, "raster_share": 0.3}

DRAWING_FIELDS = {
    "designation": "обозначение",
    "name": "наименование",
    "material": "материал",
    "mass": "масса",
    "scale": "масштаб",
    "litera": "литера",
    "revision": "изм.",
    "archive_number": "инв. № подл.",
    "first_use": "перв. примен.",
    "sheets": "листов",
}
SPEC_FIELDS = {
    "designation": "обозначение",
    "name": "наименование",
    "litera": "литера",
    "revision": "изм.",
    "sheets": "листов",
}
ROW_FIELDS = {"designation": "designation", "name": "name", "qty": "quantity"}  # the truth: the report row
FIELD_LABELS = {
    **{f"drawing.{k}": f"Основная надпись чертежа: {v}" for k, v in DRAWING_FIELDS.items()},
    **{f"spec.{k}": f"Основная надпись спецификации: {v}" for k, v in SPEC_FIELDS.items()},
    "requirement": "Пункт технических требований",
    "row.designation": "Строка спецификации: обозначение",
    "row.name": "Строка спецификации: наименование",
    "row.qty": "Строка спецификации: количество",
}

MISSED_CELL = "Значение не прочитано"
MISSED_LINE = "Строка пропущена"
EXTRA_VALUE = "Лишнее значение"
EXTRA_LINE = "Лишняя строка"
MADE_UP = "Значение достроено моделью"
WRONG_REGION = "Неверная область"
TWIN = "Символ-двойник"
LOST_SIGN = "Потерян знак"
SPECIAL = "Спецсимвол"
LABEL_GLUED = "Подпись графы склеена со значением"
NEIGHBOUR = "Захвачен соседний текст"
MERGED = "Пункты склеены"
EXTRA_SIGN = "Лишний знак"
LOSSY = "Строка прочитана с потерями"
WRONG = "Неверное значение"
KINDS = (
    TWIN, LOST_SIGN, SPECIAL, LABEL_GLUED, NEIGHBOUR, EXTRA_SIGN, LOSSY, WRONG, MISSED_CELL, MISSED_LINE, MERGED,
    MADE_UP, EXTRA_VALUE, EXTRA_LINE, WRONG_REGION,
)  # fmt: skip
SMALL = (SPECIAL, TWIN, LOST_SIGN, EXTRA_SIGN)  # changes of a value read almost right, the first wins a tie
LOST_CHAR = "Потеряна буква или цифра"
EXTRA_CHAR = "Лишняя буква или цифра"
OTHER_CHAR = "Замена нескольких символов"
CHANGES = (*SMALL, LOST_CHAR, EXTRA_CHAR, OTHER_CHAR)
ALIGNED = (*SMALL, LOSSY)  # errors whose characters line up with those of the truth

SYMBOLS = set("⌀Øø√±°×∠⊥∥⌒⌓◎⊕↗▱△□")  # GOST signs of dimensions, roughness and form tolerances
LIGHT = {k: v for k, v in scoring.FOLD.items() if v}  # the fold of scoring.norm that keeps the diameter sign
_settings = load_settings()
LABELS = sorted(
    set(_settings.forms.title_block.people.labels)
    | _settings.forms.title_block.label_words
    | {_settings.forms.title_block.margin.archive_label, _settings.forms.title_block.margin.first_use_label},
    key=len,
    reverse=True,
)
GROUPS = {
    "vector": "вектор",
    "raster:good": "растр, хорошее",
    "raster:fair": "растр, среднее",
    "raster:poor": "растр, плохое",
    "scan:good": "скан, хорошее",
    "scan:fair": "скан, среднее",
    "scan:poor": "скан, плохое",
    "augraphy:good": "скан Augraphy, хорошее",
    "augraphy:fair": "скан Augraphy, среднее",
    "augraphy:poor": "скан Augraphy, плохое",
}
INPUTS = {"vector": "вектор", "raster": "растр", "scan": "скан", "augraphy": "скан Augraphy"}


@dataclasses.dataclass
class Record:
    corpus: str
    kit: str
    document: str
    group: str  # vector, raster:good, scan:poor, augraphy:fair…
    page: int | None
    field: str
    region: tuple[float, float, float, float] | None  # of the truth, shares of the page
    truth: str
    read: str
    engine: str
    confidence: float | None
    status: str
    right: bool
    read_region: tuple[float, float, float, float] | None = None
    kind: str | None = None

    @property
    def input(self) -> str:
        return self.group.split(":")[0]


def fold(text: object) -> str:
    """The value as its kind of error is told: :func:`scoring.norm` that keeps the diameter sign."""
    return re.sub(r"\s+", "", str(text or "").translate(LIGHT).lower().replace("ё", "е").replace(",", "."))


def changes(want: str, got: str) -> list[tuple[str, str, str]]:
    """Each change of the characters of the truth into those read, after :func:`fold`: its kind, what it cut, what it
    put. A sign is a character that is no letter, digit or symbol of :data:`SYMBOLS`; a twin is one or two characters
    read as as many others."""
    a, b = fold(want), fold(got)
    found = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        cut, put = a[i1:i2], b[j1:j2]
        if tag == "equal":
            continue
        if SYMBOLS & set(cut + put):
            what = SPECIAL
        elif tag == "delete":
            what = LOST_CHAR if any(c.isalnum() for c in cut) else LOST_SIGN
        elif tag == "insert":
            what = EXTRA_CHAR if any(c.isalnum() for c in put) else EXTRA_SIGN
        else:
            what = TWIN if len(cut) == len(put) <= 2 else OTHER_CHAR
        found.append((what, cut, put))
    return found


def kind(want: str, got: str, *, model: bool = False, line: bool = False, others: frozenset[str] = frozenset()) -> str:
    """The kind of error of reading ``got`` for the value ``want`` of the truth; ``others`` are the values of the other
    fields of the document, normalized.

    A value read almost right takes the kind of most of its changes: twins of at most a quarter of the characters,
    lost or extra signs, special symbols. One that holds the whole value has text of a neighbour or the label of its
    cell glued to it; the rest are read with losses, or wrong when less than half of them is alike."""
    if not scoring.norm(got):
        return MISSED_LINE if line else MISSED_CELL
    if not scoring.norm(want):
        return MADE_UP if model else (EXTRA_LINE if line else EXTRA_VALUE)
    if len(scoring.norm(got)) >= 3 and scoring.norm(got) in others:
        return WRONG_REGION
    a, b = fold(want), fold(got)
    if a in b:
        rest = b.replace(a, "", 1)
        if not any(c.isalnum() for c in rest):
            return EXTRA_SIGN
        letters = re.sub(r"[^а-яa-z]", "", rest)
        return LABEL_GLUED if letters and any(letters.startswith(label) for label in LABELS) else NEIGHBOUR
    found = changes(want, got)
    twin_chars = sum(len(cut) for what, cut, _ in found if what == TWIN)
    if all(what in SMALL for what, _, _ in found) and twin_chars <= max(2, len(a) // 4):
        counts = Counter(what for what, _, _ in found)
        return max(SMALL, key=lambda k: (counts[k], -SMALL.index(k)))
    if SequenceMatcher(None, a, b, autojunk=False).ratio() < 0.5:
        return MADE_UP if model else WRONG
    return LOSSY


def covered(want: str, got: object) -> float:
    """The share of the characters of ``want`` that ``got`` holds in their order."""
    a, b = fold(want), fold(got)
    return sum(m.size for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / max(1, len(a))


def twins(want: str, got: str) -> list[tuple[str, str]]:
    """The characters of the truth and what was read in their place, in the twins among the changes."""
    return [pair for what, cut, put in changes(want, got) if what == TWIN for pair in zip(cut, put, strict=True)]


def _text(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # a quantity of the report is a number, «2.0», where the sheet writes «2»
    return "" if value is None else str(value).replace("\n", " ")


def _engine(value: Extracted | None) -> str:
    src = value.source if value is not None else None
    if src is None:
        return ""
    return f"{src.method or ''}:{src.engine or ''}".strip(":")


def _confidence(value: Extracted | None) -> float | None:
    if value is None:
        return None
    if value.confidence is not None:
        return value.confidence
    return value.source.confidence if value.source is not None else None


def _region(value: Extracted | None) -> scoring.Region | None:
    src = value.source if value is not None else None
    return (src.page, src.bbox) if src is not None and src.bbox is not None else None


def _centre_in(bbox: tuple[float, float, float, float], box: Box) -> bool:
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return box.bbox[0] <= x <= box.bbox[2] and box.bbox[1] <= y <= box.bbox[3]


def _all_boxes(doc: DrawingTruth | SpecTruth) -> list[scoring.Region]:
    boxes: list[Box | None] = [*doc.boxes.values()]
    if isinstance(doc, SpecTruth):
        boxes += [b for row in doc.rows for b in row.boxes.values()]
    else:
        boxes += [*doc.requirement_boxes, *(p.box for p in doc.parameters)]
    return [(b.page, b.bbox) for b in boxes if b is not None]


class Scorer:
    """Records of one document: each value of the truth against the value of the report."""

    def __init__(self, corpus: str, kit: str, path: str, doc: DrawingTruth | SpecTruth) -> None:
        self.base = {"corpus": corpus, "kit": kit, "document": path}
        self.group = "vector" if doc.capture is None else doc.capture.group
        self.boxes = _all_boxes(doc)
        self.records: list[Record] = []

    def value(
        self,
        field: str,
        want: object,
        got: Extracted | None,
        box: Box | None,
        *,
        line: bool,
        others: frozenset[str],
        merged: bool = False,
    ) -> None:
        read = _text(got.value if got is not None else None)
        right = scoring.norm(read) == scoring.norm(want)
        if right and not scoring.norm(want):
            return
        record = Record(
            **self.base,
            group=self.group,
            page=box.page if box is not None else (got.source.page if got and got.source else None),
            field=field,
            region=box.bbox if box is not None else None,
            truth=_text(want),
            read=read,
            engine=_engine(got),
            confidence=_confidence(got),
            status=(got.status or "") if got is not None else "",
            right=right,
            read_region=got.source.bbox if got is not None and got.source is not None else None,
        )
        if not right:
            model = got is not None and got.source is not None and got.source.method == "vlm"
            record.kind = MERGED if merged else kind(record.truth, read, model=model, line=line, others=others)
        elif box is not None and _engine(got).split(":")[0] not in ("filename", "rule", "manual"):
            if scoring.region_problem((box.page, box.bbox), _region(got), self.boxes) is not None:
                record.kind = WRONG_REGION
        self.records.append(record)

    def fields(self, prefix: str, names: dict[str, str], doc: DrawingTruth | SpecTruth, got: Any) -> None:
        values = {name: scoring.norm(getattr(doc, name)) for name in names}
        for name in names:
            others = frozenset(v for k, v in values.items() if k != name and v)
            cell = getattr(got, name) if got is not None else None
            self.value(f"{prefix}.{name}", getattr(doc, name), cell, doc.boxes.get(name), line=False, others=others)

    def requirements(self, doc: DrawingTruth, got: list[Extracted]) -> None:
        pairs = pair_items(doc.requirements, doc.requirement_boxes, got)
        everything = frozenset(scoring.norm(r) for r in doc.requirements)
        for i, (want, box) in enumerate(zip(doc.requirements, doc.requirement_boxes, strict=True)):
            others = everything - {scoring.norm(want)}
            if pairs[i] is not None:
                self.value("requirement", want, got[pairs[i]], box, line=True, others=others)
                continue
            holder = max(got, key=lambda item: covered(want, item.value), default=None)
            if holder is not None and covered(want, holder.value) >= 0.7:
                self.value("requirement", want, holder, box, line=True, others=others, merged=True)
            else:
                self.value("requirement", want, None, box, line=True, others=others)
        for j in sorted(set(range(len(got))) - {p for p in pairs if p is not None}):
            self.value("requirement", "", got[j], None, line=True, others=everything)

    def rows(self, doc: SpecTruth, got: list[SpecRow]) -> None:
        pairs = pair_rows(doc.rows, got)
        for want, row in zip(doc.rows, pairs, strict=True):
            for field, attr in ROW_FIELDS.items():
                value = getattr(want, field)
                if field == "qty" and want.quantities:
                    continue
                others = frozenset(scoring.norm(getattr(r, field)) for r in doc.rows if r is not want) | frozenset(
                    scoring.norm(getattr(want, f)) for f in ROW_FIELDS if f != field
                )
                cell = getattr(row, attr) if row is not None else None
                if field == "designation" and want.written and cell is not None:
                    if scoring.norm(cell.value) == scoring.norm(want.written):
                        value = want.written
                self.value(f"row.{field}", value, cell, want.boxes.get(field), line=row is None, others=others)
        for row in got:
            if row not in pairs and row.designation is not None and row.designation.value not in (None, ""):
                self.value("row.designation", "", row.designation, None, line=True, others=frozenset())


def pair_items(want: list[str], boxes: list[Box | None], got: list[Extracted]) -> list[int | None]:
    """For each item of the truth, the index of the item of the report read for it, one to one: by the region first,
    then by how alike they are."""
    candidates = []
    for i, (text, box) in enumerate(zip(want, boxes, strict=True)):
        for j, item in enumerate(got):
            ratio = SequenceMatcher(None, fold(text), fold(item.value), autojunk=False).ratio()
            region = _region(item)
            inside = box is not None and region is not None and region[0] == box.page and _centre_in(region[1], box)
            if inside or ratio >= 0.5:
                candidates.append((inside + ratio, i, j))
    pairs: list[int | None] = [None] * len(want)
    used: set[int] = set()
    for _, i, j in sorted(candidates, reverse=True):
        if pairs[i] is None and j not in used:
            pairs[i] = j
            used.add(j)
    return pairs


def pair_rows(want: list[RowTruth], got: list[SpecRow]) -> list[SpecRow | None]:
    """For each row of the truth, the row of the report in its band on the sheet, one to one."""
    used: set[int] = set()
    pairs: list[SpecRow | None] = []
    for row in want:
        boxes = list(row.boxes.values())
        match = None
        if boxes:
            page, top, bottom = boxes[0].page, min(b.bbox[1] for b in boxes), max(b.bbox[3] for b in boxes)
            match = next((i for i, r in enumerate(got) if i not in used and _row_at(r, page, top, bottom)), None)
        if match is not None:
            used.add(match)
        pairs.append(got[match] if match is not None else None)
    return pairs


def _row_at(row: SpecRow, page: int, top: float, bottom: float) -> bool:
    for cell in (row.designation, row.name, row.position, row.quantity, row.format):
        if cell is not None and cell.source is not None and cell.source.bbox is not None:
            bbox = cell.source.bbox
            return cell.source.page == page and top <= (bbox[1] + bbox[3]) / 2 <= bottom
    return False


def measured(corpus: str, doc: DrawingTruth | SpecTruth) -> bool:
    """Whether the document counts: of the stress corpus only the scans of Augraphy, its other documents are those of
    the frozen corpus."""
    return corpus != "stress" or (doc.capture is not None and doc.capture.degrader == "augraphy")


def score(corpus: str, kit: str, report: Report, truth: KitTruth) -> list[Record]:
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    specs = {s.document_id: s for s in report.specifications}
    records = []
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth | SpecTruth) or not measured(corpus, doc):
            continue
        scorer = Scorer(corpus, kit, path, doc)
        if isinstance(doc, DrawingTruth):
            part = parts.get(ids.get(path, ""))
            scorer.fields("drawing", DRAWING_FIELDS, doc, part.title_block if part else None)
            scorer.requirements(doc, list(part.requirements) if part else [])
        else:
            spec = specs.get(ids.get(path, ""))
            scorer.fields("spec", SPEC_FIELDS, doc, spec)
            scorer.rows(doc, list(spec.rows) if spec else [])
        records += scorer.records
    return records


def document_seconds(events: list[tuple[float, str, str | None]], paths: set[str]) -> dict[str, float]:
    """Seconds each document took to load and to parse, by the progress of the pipeline: the time from the call that
    names it to the next call."""
    seconds: Counter[str] = Counter()
    for (at, _, note), (then, _, _) in zip(events, events[1:], strict=False):
        if note in paths:
            seconds[note] += then - at
    return dict(seconds)


def build() -> None:
    commit = commit_of_generator()
    if CORPUS.exists() and any(CORPUS.iterdir()):
        raise SystemExit(f"Папка {CORPUS} не пуста: корпус собирается в пустую папку")
    CORPUS.mkdir(parents=True, exist_ok=True)
    built = kits.corpus(
        CORPUS,
        PARAMETERS["kits"],
        PARAMETERS["seed"],
        clean_every=PARAMETERS["clean_every"],
        scan_share=PARAMETERS["scan_share"],
        raster_share=PARAMETERS["raster_share"],
    )
    captures = Counter(
        "vector" if d.capture is None else d.capture.group
        for t in built
        for d in t.documents.values()
        if isinstance(d, DrawingTruth | SpecTruth)
    )
    manifest = {
        "generator": "synth.kits",
        "built": datetime.now(UTC).date().isoformat(),
        "parameters": PARAMETERS,
        "commit": commit,
        "source": source_digest(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "versions": {name: importlib.metadata.version(name) for name in LIBRARIES},
        "documents": dict(sorted(captures.items())),
        "files": digests(CORPUS),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Корпус: {len(manifest['files'])} файлов, {PARAMETERS['kits']} комплектов; манифест {MANIFEST}")


def corpora(stress: bool) -> Iterator[tuple[str, Path, KitTruth]]:
    """The kits to measure after their files match their manifests: the frozen corpus, then the stress corpus."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    found = digests(CORPUS) if CORPUS.is_dir() else {}
    if found != manifest["files"]:
        missing = len(set(manifest["files"]) - set(found))
        raise SystemExit(f"Корпус {CORPUS} не совпадает с манифестом {MANIFEST} (нет файлов: {missing})")
    for i in range(1, manifest["parameters"]["kits"] + 1):
        name = f"kit_{i:03d}"
        yield "synthetic", CORPUS / name, KitTruth.read(CORPUS / f"{name}_truth.json")
    if not stress:
        return
    if problems := verify_stress(STRESS, StressManifest.read(STRESS_MANIFEST)):
        raise SystemExit(f"Стрессовый корпус {STRESS} не совпадает с манифестом: {problems[:5]}")
    for i in range(1, StressManifest.read(STRESS_MANIFEST).parameters.kits + 1):
        name = f"kit_{i:03d}"
        yield "stress", STRESS / name, KitTruth.read(STRESS / f"{name}_truth.json")


def run(vlm: bool, cache: bool, stress: bool, rescore: bool, suffix: str) -> dict[str, Any]:
    """The records of a run of the core over the corpora, with the time per sheet. The reports and times are kept in
    data/reading/reports; ``rescore`` takes them from there instead of running the core."""
    saved = RECORDS / "reports"
    settings = load_settings(use_ocr=True, use_vlm=vlm)
    lines = settings.models.ocr.lines.engine
    if rescore:
        info = json.loads((saved / f"run{suffix}.json").read_text(encoding="utf-8"))
    else:
        if not ocr_available(settings):
            raise SystemExit("Нет Tesseract с языками rus+eng: замер чтения без OCR не имеет смысла")
        if not (ROOT / "models" / "ppocr").is_dir():
            raise SystemExit("Нет models/ppocr: ячейки прочтёт Tesseract, и цифры не сравнить (scripts/fetch_ppocr.sh)")
        info = {
            "date": datetime.now(UTC).date().isoformat(),
            "reader": (f"OCR и модель {settings.models.vlm.model}" if vlm else "OCR")
            + (", строки чертежа — PP-OCRv5 с Tesseract" if lines == "ppocr" else ", строки чертежа — Tesseract"),
            "cache": cache,
            "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
            "engines": {},
        }
        saved.mkdir(parents=True, exist_ok=True)
    pipeline = BaselinePipeline(settings)
    records: list[Record] = []
    sheets: dict[str, list[float]] = defaultdict(list)
    started = time.monotonic()
    with read_cache.use(CACHE if cache else None):
        for corpus, folder, truth in corpora(stress):
            kept = saved / f"{corpus}_{folder.name}{suffix}.json"
            if rescore:
                data = json.loads(kept.read_text(encoding="utf-8"))
                report, seconds = Report.model_validate(data["report"]), data["seconds"]
            else:
                events: list[tuple[float, str, str | None]] = []
                report = pipeline.run(
                    folder,
                    kit_id=folder.name,
                    kit_name=folder.name,
                    progress=lambda stage, _, note, events=events: events.append((time.monotonic(), stage, note)),
                )
                if vlm and pipeline.vlm_problem:
                    raise SystemExit(f"Модель недоступна: {pipeline.vlm_problem}")
                info["engines"] |= {e.name: e.version or "" for e in report.kit.engines}
                info["config_sha256"] = report.kit.config_sha256
                seconds = document_seconds(events, {d.path for d in report.documents})
                kept.write_text(
                    json.dumps({"report": report.model_dump(mode="json"), "seconds": seconds}, ensure_ascii=False),
                    encoding="utf-8",
                )
            pages = {d.path: len(d.pages) for d in report.documents}
            for path, took in seconds.items():
                doc = truth.documents.get(path)
                if isinstance(doc, DrawingTruth | SpecTruth) and measured(corpus, doc) and pages[path]:
                    group = "vector" if doc.capture is None else doc.capture.group
                    for key in {group, group.split(":")[0], "all"}:
                        sheets[f"{corpus}/{key}"] += [took / pages[path]] * pages[path]
            records += score(corpus, folder.name, report, truth)
            print(f"{corpus}/{folder.name}: {sum(r.kind is not None for r in records)} ошибок нарастающим итогом")
    if not rescore:
        info["seconds"] = round(time.monotonic() - started, 1)
        (saved / f"run{suffix}.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return info | {
        "corpus": {
            "manifest": MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        },
        "sheet_seconds": {k: round(statistics.mean(v), 3) for k, v in sorted(sheets.items())},
        "records": records,
    }


def blocks_missed(records: list[Record]) -> dict[str, int]:
    """Drawings none of whose items of technical requirements the report gives, and how many items they hold."""
    items: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for r in records:
        if r.field == "requirement" and r.truth:
            items[(r.kit, r.document)].append(r)
    missed = [v for v in items.values() if all(r.kind == MISSED_LINE for r in v)]
    return {"drawings": len(missed), "items": sum(len(v) for v in missed)}


def summarize(records: list[Record]) -> dict[str, Any]:
    """Shares of right values, the kinds of errors by input, class and field, and the characters read for others."""
    by_corpus: dict[str, Any] = {}
    for corpus in sorted({r.corpus for r in records}, key=lambda c: c != "synthetic"):
        mine = [r for r in records if r.corpus == corpus]
        truths = [r for r in mine if r.truth]
        shares: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for r in truths:
            what = "title_block" if r.field.split(".")[0] in ("drawing", "spec") else r.field.split(".")[0]
            for key in {r.input, r.group, "all"}:
                shares[what][key][0] += r.right
                shares[what][key][1] += 1
        errors = [r for r in mine if r.kind is not None]
        twin_pairs: Counter[str] = Counter()
        changed: dict[str, Counter[str]] = defaultdict(Counter)
        for r in errors:
            if r.kind in ALIGNED:
                twin_pairs.update(f"{a}→{b}" for a, b in twins(r.truth, r.read))
                for what, _, _ in changes(r.truth, r.read):
                    changed[r.input][what] += 1
                    changed["all"][what] += 1
        by_corpus[corpus] = {
            "values": len(truths),
            "extra": sum(not r.truth for r in mine),
            "blocks_missed": blocks_missed(truths),
            "errors": len(errors),
            "right": {what: {k: v for k, v in sorted(counts.items())} for what, counts in shares.items()},
            "kinds": dict(Counter(r.kind for r in errors).most_common()),
            "by_input": {k: dict(Counter(r.kind for r in errors if r.input == k)) for k in INPUTS},
            "by_group": {g: dict(Counter(r.kind for r in errors if r.group == g)) for g in GROUPS},
            "by_field": {
                f: {
                    "values": sum(r.field == f for r in truths),
                    "kinds": dict(Counter(r.kind for r in errors if r.field == f)),
                }
                for f in FIELD_LABELS
            },
            "by_engine": {
                e: dict(Counter(r.kind for r in errors if r.engine == e)) for e in sorted({r.engine for r in errors})
            },
            "twins": dict(twin_pairs.most_common(30)),
            "changes": {k: dict(v.most_common()) for k, v in changed.items()},
        }
    return by_corpus


COLUMNS = (
    ("corpus", "корпус"),
    ("kit", "комплект"),
    ("document", "документ"),
    ("page", "лист"),
    ("field", "поле"),
    ("region", "область"),
    ("truth", "верное значение"),
    ("read", "прочитано"),
    ("engine", "движок"),
    ("confidence", "уверенность"),
    ("kind", "вид ошибки"),
    ("group", "вход и класс"),
    ("status", "статус"),
    ("read_region", "область прочитанного"),
    ("right", "верно"),
)


def write_csv(path: Path, records: list[Record]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([label for _, label in COLUMNS])
        for r in records:
            row = []
            for name, _ in COLUMNS:
                value = getattr(r, name)
                if name == "field":
                    value = FIELD_LABELS[value]
                elif name in ("region", "read_region") and value is not None:
                    value = " ".join(f"{v:.4f}" for v in value)
                elif name == "group":
                    value = GROUPS.get(value, value)
                row.append("" if value is None else value)
            writer.writerow(row)


def _share(right: int, total: int) -> str:
    return f"{right}/{total} ({right / total:.3f})".replace(".", ",") if total else "—"


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    return lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]


def markdown(result: dict[str, Any], summary: dict[str, Any], command: str) -> str:
    lines = [
        "# Ошибки чтения на синтетике",
        "",
        f"Получено `{command}` {result['date']}; чтение — {result['reader']}; {result['machine']}. Движки: "
        + ", ".join(f"{k} {v}".strip() for k, v in result["engines"].items())
        + "."
        + (f" Настройки — sha256 {result['config_sha256'][:12]}." if result.get("config_sha256") else "")
        + (" Прочтения модели хранятся в кэше: время — время первого чтения." if result["cache"] else ""),
        "",
        f"Корпус заморожен манифестом [{MANIFEST.name}](../{MANIFEST.name}): "
        "чистые комплекты `synth.kits` с долями растров и сканов, как у `scan_quality.py`. Запись — значение разметки: "
        "поле основной надписи чертежа или спецификации, пункт технических требований, обозначение, наименование и "
        "количество строки спецификации. Значение верно по общему правилу (`scoring.norm`); верное значение с областью "
        "не там, где оно на листе, — ошибка вида «Неверная область». Пункты и строки, которых нет в разметке, — "
        "записи вида «Лишняя строка». Все записи ошибок в общем формате — "
        f"[{result['csv']}]({result['csv']}).",
        "",
    ]
    for corpus, s in summary.items():
        title = "Синтетика" if corpus == "synthetic" else "Стрессовый корпус Augraphy (только замер)"
        lines += [f"## {title}", ""]
        blocks = s["blocks_missed"]
        lines += [
            f"Значений разметки {s['values']}, лишних записей {s['extra']}, ошибок {s['errors']}. Чертежей, у "
            f"которых не найдено ни одного пункта технических требований: {blocks['drawings']}, пунктов в них: "
            f"{blocks['items']}.",
            "",
        ]
        groups = [g for g in GROUPS if g != "vector" and any(g in v for v in s["right"].values())]
        inputs = [i for i in INPUTS if any(i in v for v in s["right"].values())]
        what = (
            ("title_block", "Поля основной надписи"),
            ("requirement", "Пункты ТТ"),
            ("row", "Ячейки строк спецификаций"),
        )
        lines += ["### Доля верных значений", ""]
        rows = []
        for key in [*inputs, *groups, "all"]:
            label = INPUTS.get(key) or GROUPS.get(key) or "все"
            cells = [_share(*s["right"].get(w, {}).get(key, (0, 0))) for w, _ in what]
            seconds = result["sheet_seconds"].get(f"{corpus}/{key}")
            rows.append([label, *cells, "—" if seconds is None else f"{seconds:g}".replace(".", ",")])
        lines += _table(["Вход", *(t for _, t in what), "Время на лист, с"], rows) + [""]
        lines += [
            "Время на лист — время загрузки и разбора документа, делённое на его листы, в среднем по листам; "
            "сверка комплекта и Блок 2 не входят.",
            "",
            "### Виды ошибок",
            "",
        ]
        kinds = [k for k in KINDS if k in s["kinds"]]
        top = list(s["kinds"])[:3]
        listed = ", ".join(f"«{k}» ({s['kinds'][k]})" for k in top)
        lines += [f"Три самых частых вида: {listed}.", ""]
        head = ["Вид ошибки", "Всего", *(INPUTS[i] for i in inputs)]
        rows = [[k, s["kinds"][k], *(s["by_input"][i].get(k, 0) for i in inputs)] for k in kinds]
        lines += _table(head, rows) + ["", "### По классу качества", ""]
        rows = [[k, *(s["by_group"][g].get(k, 0) for g in groups)] for k in kinds]
        lines += _table(["Вид ошибки", *(GROUPS[g] for g in groups)], rows) + ["", "### По полям", ""]
        rows = []
        for f, v in s["by_field"].items():
            if v["values"] or v["kinds"]:
                common = max(v["kinds"], key=v["kinds"].get) if v["kinds"] else "—"
                rows.append([FIELD_LABELS[f], v["values"], sum(v["kinds"].values()), common])
        lines += _table(["Поле", "Значений", "Ошибок", "Самый частый вид"], rows) + ["", "### По движку", ""]
        rows = [[e or "нет значения", sum(v.values()), max(v, key=v.get)] for e, v in s["by_engine"].items()]
        lines += _table(["Движок", "Ошибок", "Самый частый вид"], rows) + [""]
        if s["changes"]:
            lines += [
                "### Изменения символов",
                "",
                "Значение, прочитанное почти верно или с потерями, разложено на изменения символов разметки, после "
                "свёртки латиницы и пробелов: в одном значении их может быть несколько.",
                "",
            ]
            cols = [i for i in INPUTS if i in s["changes"]]
            rows = [[c, *(s["changes"][i].get(c, 0) for i in cols)] for c in CHANGES if c in s["changes"]["all"]]
            lines += _table(["Изменение", *(INPUTS[i] for i in cols)], rows) + [""]
        if s["twins"]:
            lines += [
                "### Символы-двойники",
                "",
                "Буквы и цифры разметки и что прочитано на их месте в тех же значениях.",
                "",
            ]
            lines += _table(["Верно → прочитано", "Случаев"], [[k, v] for k, v in list(s["twins"].items())[:20]]) + [""]
    lines += ["## Примеры", "", "По три ошибки каждого вида на синтетике, по порядку записей.", ""]
    rows = []
    for k in KINDS:
        for r in [r for r in result["records"] if r.kind == k and r.corpus == "synthetic"][:3]:
            conf = "—" if r.confidence is None else f"{r.confidence:.2f}".replace(".", ",")
            rows.append(
                [k, FIELD_LABELS[r.field], GROUPS.get(r.group, r.group), f"«{r.truth}»", f"«{r.read}»", r.engine, conf]
            )
    lines += _table(["Вид", "Поле", "Вход", "Верно", "Прочитано", "Движок", "Уверенность"], rows)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="собрать корпус в data/synth/reading и записать манифест")
    measure = sub.add_parser("run", help="прогнать ядро и записать ошибки")
    measure.add_argument("--vlm", action="store_true", help="OCR и модель на локальном сервере (ADR-0019)")
    measure.add_argument("--cache", action="store_true", help="брать и хранить прочтения в data/cache (ADR-0018)")
    measure.add_argument("--stress", action="store_true", help="и стрессовый корпус Augraphy, только замер")
    measure.add_argument("--score", action="store_true", help="пересчитать по отчётам прошлого прогона без ядра")
    measure.add_argument("--tag", help="метка прогона в именах файлов: reading-errors-<метка>.md")
    args = parser.parse_args(argv)
    if args.command == "build":
        build()
        return 0
    suffix = ("-vlm" if args.vlm else "") + (f"-{args.tag}" if args.tag else "")
    result = run(args.vlm, args.cache, args.stress, args.score, suffix)
    records = result["records"]
    result["csv"] = f"reading-errors{suffix}.csv"
    write_csv(OUT / result["csv"], [r for r in records if r.kind is not None])
    write_csv(RECORDS / f"records{suffix}.csv", records)
    summary = summarize(records)
    flags = "".join(f" --{f}" for f in ("vlm", "cache", "stress") if getattr(args, f))
    command = f"uv run python eval/reading_errors.py run{flags}" + (f" --tag {args.tag}" if args.tag else "")
    (OUT / f"reading-errors{suffix}.md").write_text(markdown(result, summary, command), encoding="utf-8")
    saved = {k: v for k, v in result.items() if k != "records"} | {"summary": summary}
    (OUT / f"reading-errors{suffix}.json").write_text(
        json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for corpus, s in summary.items():
        print(corpus, s["values"], "значений,", s["errors"], "ошибок:", list(s["kinds"].items())[:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
