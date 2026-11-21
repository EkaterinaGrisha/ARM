"""The core on a customer's kit against its settled gold, data/arm/gold/<kit>.json (plan, step 6), in numbers only.

Block 1: the discrepancies of the gold found, missed and extra by type; one is found when the report has one of the
same type with the same designation after the normalization of the core, as ``eval/gold.py kits`` counts. A gold
without discrepancies, as a test kit has, gives the findings only: each is reviewed against the sheets. Block 2: every
value of the gold against the report by the rule of scoring.py, recorded as eval/reading_errors.py records it: title
block fields, items of technical requirements, rows of specifications; parameters by category, from the items and
from the field apart; regions of items and parameters. The gold of a customer's kit has no boxes of rows, so rows
pair by designation, then by name. Only the documents of the gold count: a gold of a sample of sheets is measured on
the sample.

The customer's values stay in data/arm/eval, outside git: the report, every record and the findings. Into
eval/results/arm-gold-<kit>[-vlm].json and .md go the numbers, with the right and wrong values the report gives by
status, method and confidence: the data the review mark is calibrated on.

    uv run python eval/arm_gold.py arm-1 [--vlm] [--cache] [--rescore]
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import platform
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import scoring
from kdcheck import cache as read_cache
from kdcheck.ingest.document import kit_files
from kdcheck.normalize.designations import Designations
from kdcheck.pipeline import BaselinePipeline
from kdcheck.report.labels import DISCREPANCY_TYPES, PARAMETER_CATEGORY
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Report, SpecRow
from reading_errors import DRAWING_FIELDS, SPEC_FIELDS, Record, Scorer
from synth.truth import DrawingTruth, KitTruth, RowTruth, SpecTruth
from synth_kits import regions

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "data" / "arm"
PRIVATE = ARM / "eval"
CACHE = ROOT / "data" / "cache"
OUT = Path(__file__).resolve().parent / "results"
BINS = (0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)  # lower bounds of the confidence bins


def report_of(kit: str, vlm: bool, cache: bool, rescore: bool) -> tuple[Report, Designations, dict[str, Any]]:
    """The report of the core on the kit, kept in data/arm/eval, and the designations scoped to the kit."""
    folder = ARM / json.loads((ARM / "kits.json").read_text(encoding="utf-8"))[kit]["path"]
    settings = load_settings(use_ocr=True, use_vlm=vlm)
    pipeline = BaselinePipeline(settings)
    files, _ = kit_files(folder, settings.ingest.file_names)
    eskd = pipeline.designations.for_kit([p.stem for p in files])
    kept = PRIVATE / f"{kit}{'-vlm' if vlm else ''}.report.json"
    if rescore:
        report = Report.model_validate_json(kept.read_text(encoding="utf-8"))
        return report, eskd, json.loads(kept.with_suffix(".run.json").read_text(encoding="utf-8"))
    if not ocr_available(settings):
        raise SystemExit("Нет Tesseract с языками rus+eng: замер без OCR не имеет смысла")
    if not (ROOT / "models" / "ppocr").is_dir():
        raise SystemExit("Нет models/ppocr: ячейки прочтёт Tesseract, и цифры не сравнить (scripts/fetch_ppocr.sh)")
    started = datetime.now(UTC)
    with read_cache.use(CACHE if cache else None):
        report = pipeline.run(folder, kit_id=kit, kit_name=kit)
    if vlm and pipeline.vlm_problem:
        raise SystemExit(f"Модель недоступна: {pipeline.vlm_problem}")
    run = {
        "date": started.date().isoformat(),
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "reader": f"OCR и модель {settings.models.vlm.model}" if vlm else "OCR",
        "cache": cache,
        "config_sha256": report.kit.config_sha256,
        "engines": {e.name: e.version or "" for e in report.kit.engines},
    }
    PRIVATE.mkdir(parents=True, exist_ok=True)
    kept.write_text(report.model_dump_json() + "\n", encoding="utf-8")
    kept.with_suffix(".run.json").write_text(json.dumps(run, ensure_ascii=False) + "\n", encoding="utf-8")
    return report, eskd, run


def block1(report: Report, truth: KitTruth, eskd: Designations) -> tuple[dict[str, Any], list[str]]:
    """Found, missed and extra discrepancies by type, and the lines of the findings for the review."""

    def key(kind: str, designation: str | None) -> tuple[str, str]:
        return kind, eskd.normalize_designation(designation) or (designation or "").strip()

    want = Counter(key(d.type, d.designation or d.name) for d in truth.discrepancies)
    got = Counter(key(d.type, d.designation) for d in report.discrepancies)
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    for (kind, _), n in want.items():
        by_type[kind]["expected"] += n
    for (kind, _), n in (want & got).items():
        by_type[kind]["found"] += n
    for (kind, _), n in (got - want).items():
        by_type[kind]["extra"] += n
    labelled = bool(truth.discrepancies)
    left = Counter(want)
    lines = []
    for d in sorted(report.discrepancies, key=lambda d: (d.type, d.designation or "")):
        mark = "разобрать"
        if labelled:
            mark = "верно" if left[key(d.type, d.designation)] > 0 else "лишнее"
            left[key(d.type, d.designation)] -= 1
        lines.append(
            f"| {mark} | {d.rule_id or ''} | {d.type} | {d.designation or ''} | "
            f"{'да' if d.preliminary else ''} | {d.message.replace('|', '/')} |"
        )
    lines += [f"| пропущено | | {kind} | {designation} | | |" for kind, designation in (want - got).elements()]
    result = {
        "labelled": labelled,
        "findings": len(report.discrepancies),
        "preliminary": sum(d.preliminary for d in report.discrepancies),
        "by_type": {k: dict(v) for k, v in sorted(by_type.items())},
    }
    return result, lines


def pair_rows(want: list[RowTruth], got: list[SpecRow]) -> list[SpecRow | None]:
    """For each row of the gold, the row of the report with the same designation, else with the same name, one to
    one; a row of the gold «-01» under a full designation pairs by the designation as written too."""
    free = list(got)
    pairs: list[SpecRow | None] = [None] * len(want)
    for field in ("designation", "name"):
        for i, row in enumerate(want):
            keys = {scoring.norm(getattr(row, field))} | (
                {scoring.norm(row.written)} if field == "designation" else set()
            )
            keys.discard("")
            match = next((r for r in free if getattr(r, field) and scoring.norm(getattr(r, field).value) in keys), None)
            if pairs[i] is None and match is not None:
                pairs[i] = match
                free.remove(match)
    return pairs


class KitScorer(Scorer):
    def __init__(self, kit: str, path: str, doc: DrawingTruth | SpecTruth) -> None:
        super().__init__("arm", kit, path, doc)
        self.group = doc.origin

    def rows(self, doc: SpecTruth, got: list[SpecRow]) -> None:
        pairs = pair_rows(doc.rows, got)
        for want, row in zip(doc.rows, pairs, strict=True):
            for field, attr in (("designation", "designation"), ("name", "name"), ("qty", "quantity")):
                if field == "qty" and want.quantities:
                    continue
                value = getattr(want, field)
                cell = getattr(row, attr) if row is not None else None
                if field == "designation" and want.written and cell is not None:
                    if scoring.norm(cell.value) == scoring.norm(want.written):
                        value = want.written
                self.value(f"row.{field}", value, cell, None, line=row is None, others=frozenset())
        for row in got:
            if row not in pairs and row.designation is not None and row.designation.value not in (None, ""):
                self.value("row.designation", "", row.designation, None, line=True, others=frozenset())


def block2(report: Report, truth: KitTruth) -> list[Record]:
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    specs = {s.document_id: s for s in report.specifications}
    records: list[Record] = []
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth | SpecTruth):
            continue
        scorer = KitScorer(report.kit.id, path, doc)
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


def parameters(report: Report, truth: KitTruth) -> dict[str, dict[str, int]]:
    """By «<where>:<category>», where is the items of technical requirements or the field: parameters of the gold,
    found, with numbers to check and right in them, and extra; an extra one lies in the items when its region has its
    centre in the box of an item of the gold."""
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth):
            continue
        part = parts.get(ids.get(path, ""))
        got = list(part.parameters) if part else []
        matches = scoring.match_parameters(doc.parameters, got)
        items = [b for b in doc.requirement_boxes if b is not None]
        for want, index in zip(doc.parameters, matches, strict=True):
            counts = tally[f"{'ТТ' if want.box is not None and want.box in items else 'поле'}:{want.category}"]
            counts["expected"] += 1
            if index is not None:
                counts["found"] += 1
                if scoring.has_value(want):
                    counts["valued"] += 1
                    counts["value_right"] += scoring.value_right(want, got[index])
        for i, p in enumerate(got):
            if i in matches:
                continue
            src = p.source
            inside = (
                src is not None
                and src.bbox is not None
                and any(b.page == src.page and scoring._holds(b.bbox, scoring._centre(src.bbox)) for b in items)
            )
            tally[f"{'ТТ' if inside else 'поле'}:{p.category}"]["extra"] += 1
    return {k: dict(v) for k, v in sorted(tally.items())}


def given(records: list[Record]) -> list[Record]:
    """The values the report gives: the ones the review mark can be set on."""
    return [r for r in records if scoring.norm(r.read)]


def calibration(records: list[Record]) -> dict[str, Any]:
    """Right and wrong values the report gives by status, by method and by bin of confidence, per part."""
    out: dict[str, Any] = {}
    for what, mine in parts_of(given(records)).items():
        status: dict[str, Counter[str]] = defaultdict(Counter)
        method: dict[str, Counter[str]] = defaultdict(Counter)
        bins: dict[str, Counter[str]] = defaultdict(Counter)
        for r in mine:
            verdict = "right" if r.right else "wrong"
            status[r.status or "—"][verdict] += 1
            method[r.engine.split(":")[0] or "—"][verdict] += 1
            low = max((b for b in BINS if r.confidence is not None and r.confidence >= b), default=None)
            bins["—" if low is None else f"{low:.2f}"][verdict] += 1
        out[what] = {
            name: {k: dict(v) for k, v in sorted(t.items())}
            for name, t in (("status", status), ("method", method), ("confidence", bins))
        }
    return out


def parts_of(records: list[Record]) -> dict[str, list[Record]]:
    parts: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        parts["title_block" if r.field.split(".")[0] in ("drawing", "spec") else r.field.split(".")[0]].append(r)
    return dict(parts)


PARTS = {"title_block": "основные надписи", "row": "строки спецификаций", "requirement": "пункты ТТ"}


def shares(records: list[Record]) -> dict[str, dict[str, int]]:
    """Values of the gold read right, and values the report gives beyond the gold, per part."""
    out = {}
    for what, mine in parts_of(records).items():
        truths = [r for r in mine if r.truth]
        out[what] = {
            "values": len(truths),
            "right": sum(r.right for r in truths),
            "missed": sum(not scoring.norm(r.read) for r in truths),
            "extra": sum(not r.truth for r in mine),
        }
    return out


def _share(a: int, b: int) -> str:
    return f"{a / b:.3f}".replace(".", ",") if b else "—"


def markdown(kit: str, result: dict[str, Any]) -> str:
    run, b1 = result["run"], result["block1"]
    lines = [
        f"# {kit}: ядро против эталона",
        "",
        f"Получено `uv run python eval/arm_gold.py {kit}{' --vlm' if result['vlm'] else ''}` {run['date']}; "
        f"{run['machine']}; чтение: {run['reader']}{', прочтения из кэша' if run['cache'] else ''}; "
        f"хэш настроек `{run['config_sha256'][:12]}`. Эталон — `data/arm/gold/{kit}.json`, вне git, "
        f"его sha256 `{result['gold_sha256'][:12]}`; документов в нём {result['documents']}. Верное значение — "
        "по правилу [gold/README.md](../gold/README.md#что-считается-верным-извлечением). Значения и находки "
        f"с обозначениями заказчика — в `data/arm/eval`, здесь только числа.",
        "",
        "## Блок 1",
        "",
    ]
    if b1["labelled"]:
        lines += ["| Тип | В эталоне | Найдено | Лишних |", "| --- | ---: | ---: | ---: |"]
        for kind, c in b1["by_type"].items():
            lines.append(
                f"| {DISCREPANCY_TYPES.get(kind, kind)} | {c.get('expected', 0)} | {c.get('found', 0)} | "
                f"{c.get('extra', 0)} |"
            )
        total = Counter[str]()
        for c in b1["by_type"].values():
            total.update(c)
        lines += [
            f"| всего | {total['expected']} | {total['found']} | {total['extra']} |",
            "",
            f"Находок в отчёте {b1['findings']}, предварительных {b1['preliminary']}. Полнота "
            f"{_share(total['found'], total['expected'])}, точность {_share(total['found'], b1['findings'])}.",
            "",
        ]
    else:
        lines += [
            f"Находок в отчёте {b1['findings']}, предварительных {b1['preliminary']}; эталон расхождений не "
            "называет, каждая находка разбирается по листу.",
            "",
        ]
    lines += [
        "## Блок 2",
        "",
        f"Корень дерева {'верен' if result['root'] else 'неверен или не найден'}.",
        "",
        "| Что | Значений в эталоне | Верно | Доля | Не прочитано | Лишних в отчёте |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for what, c in result["shares"].items():
        lines.append(
            f"| {PARTS.get(what, what)} | {c['values']} | {c['right']} | {_share(c['right'], c['values'])} | "
            f"{c['missed']} | {c['extra']} |"
        )
    lines += [
        "",
        "Параметры: где — пункты ТТ или поле чертежа; значение верно, если числа, единица и квалитет те, что задаёт "
        "эталон.",
        "",
        "| Где | Категория | В эталоне | Найдено | С числами | Числа верны | Лишних |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, c in result["parameters"].items():
        where, category = key.split(":")
        lines.append(
            f"| {where} | {PARAMETER_CATEGORY.get(category, category)} | {c.get('expected', 0)} | "
            f"{c.get('found', 0)} | {c.get('valued', 0)} | {c.get('value_right', 0)} | {c.get('extra', 0)} |"
        )
    lines += ["", "Области значений, которые отчёт дал и у которых в эталоне есть рамка:", ""]
    lines += [f"- {k}: верно {c.get('right', 0)} из {c.get('values', 0)}" for k, c in result["regions"].items()]
    lines += [
        "",
        "## Данные калибровки",
        "",
        "Значения, которые отчёт дал, верные и неверные: по статусу, способу чтения и уверенности. Лишние значения "
        "отчёта — неверные.",
        "",
    ]
    for what, tables in result["calibration"].items():
        lines += [
            f"### {PARTS.get(what, what).capitalize()}",
            "",
            "| Признак | Значение | Верно | Неверно | Доля неверных |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        for feature, rows in tables.items():
            for value, c in rows.items():
                right, wrong = c.get("right", 0), c.get("wrong", 0)
                lines.append(f"| {feature} | {value} | {right} | {wrong} | {_share(wrong, right + wrong)} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("kit", help="комплект АРМ с эталоном, например arm-1")
    parser.add_argument("--vlm", action="store_true", help="с моделью на сервере этой машины")
    parser.add_argument("--cache", action="store_true", help="прочтения из data/cache")
    parser.add_argument("--rescore", action="store_true", help="отчёт из data/arm/eval, без прогона ядра")
    args = parser.parse_args(argv)
    gold = ARM / "gold" / f"{args.kit}.json"
    truth = KitTruth.read(gold)
    report, eskd, run = report_of(args.kit, args.vlm, args.cache, args.rescore)
    b1, findings = block1(report, truth, eskd)
    records = block2(report, truth)
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    regions(report, truth, tally, Counter())
    root = eskd.normalize_designation(truth.root) or truth.root
    result = {
        "kit": args.kit,
        "vlm": args.vlm,
        "run": run,
        "gold_sha256": hashlib.sha256(gold.read_bytes()).hexdigest(),
        "documents": len(truth.documents),
        "root": bool(root) and (eskd.normalize_designation(report.kit.root_designation) or "") == root,
        "block1": b1,
        "shares": shares(records),
        "parameters": parameters(report, truth),
        "regions": {k.split(":", 1)[1]: dict(v) for k, v in sorted(tally.items())},
        "calibration": calibration(records),
    }
    name = f"arm-gold-{args.kit}{'-vlm' if args.vlm else ''}"
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / f"{name}.md").write_text(markdown(args.kit, result) + "\n", encoding="utf-8")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    head = [
        "| Итог | Правило | Тип | Обозначение | Предварительное | Сообщение |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    (PRIVATE / f"{name}.findings.md").write_text(
        "\n".join([f"# {args.kit}: находки", "", *head, *findings]) + "\n", encoding="utf-8"
    )
    with (PRIVATE / f"{name}.records.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[f.name for f in dataclasses.fields(Record)])
        writer.writeheader()
        writer.writerows(dataclasses.asdict(r) for r in records)
    print(f"{OUT / name}.md; находки и значения — {PRIVATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
