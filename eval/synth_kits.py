"""Blocks 1 and 2 on synthetic kits with planted errors (ADR-0013): what the core finds against the truth of each kit.

Builds the corpus of ``synth.kits`` from its seed into ``data/synth/kits`` (outside git), runs the baseline pipeline
on each kit with OCR and without, and compares the report with the truth of the kit:

- discrepancies by rule, or by type for those the core has no rule for, matched on the designation;
- the same by the mutation that planted them;
- false discrepancies on the clean kits;
- statuses of the problem files;
- parameters of the drawings by category, on vector drawings and on rasters and scans, by the rule of
  ``eval/scoring.py``: one to one by category, the name of the truth within the name, qualifier and text of the
  found parameter; of those found, whether the numbers and the unit the truth gives are right; the parameters of
  technical requirements a vector drawing loses, by the wording of their item;
- the regions of the values against the boxes of the truth, the traceability of the case: title block fields, the
  designation and name of specification rows matched by designation, and the found parameters; a value of a document
  kept one sheet per file is right in the file of its sheet only;
- documents kept one sheet per file, as ARM keeps rasters (ADR-0017): the files of later sheets the core joins to the
  first sheet of their document, with the right number of the sheet, and those it leaves apart or joins wrongly.

Writes eval/results/synth-kits.json and eval/results/synth-kits.md.

    uv run python eval/synth_kits.py [--kits 20] [--seed 1] [--sheet-files 0.5]

``--sheet-files`` is the share of kits that keep their rasters and scans one sheet per file. ``--cache`` keeps the
readings of OCR in data/cache (ADR-0018): a second run of the same corpus reads nothing again.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import scoring
from kdcheck import cache as read_cache
from kdcheck.pipeline import BaselinePipeline
from kdcheck.report.labels import PARAMETER_CATEGORY
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Extracted, Parameter, Report
from synth.truth import Box, DrawingTruth, KitTruth, SpecTruth

from synth import kits, mutations

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "synth" / "kits"
CACHE = ROOT / "data" / "cache"
OUT = Path(__file__).resolve().parent / "results"

Key = tuple[str, str]  # rule id, or the type for a discrepancy with no rule; the designation


def _expected(truth: KitTruth) -> list[tuple[Key, str | None]]:
    return [((d.rule_id or d.type, d.designation), d.mutation) for d in truth.discrepancies]


def _cause(key: Key, truth: KitTruth, *, missed: bool, use_ocr: bool) -> str:
    """A guess at why the report and the truth differ, for the error analysis."""
    rule, designation = key
    base = designation.removesuffix(" СБ")
    rows = [r for doc in truth.documents.values() if doc.kind == "specification" for r in doc.rows]
    if not missed and rule == "B1.MISSING.001" and any(r.designation == designation and r.fmt == "БЧ" for r in rows):
        return "«БЧ» в графе «Формат»: ядро ищет её только в примечании"
    if group := _group_cause(rule, base, truth, missed=missed):
        return group
    if missed and all(d.origin != "vector" for d in truth.documents.values() if isinstance(d, SpecTruth)):
        return "все спецификации комплекта — растры или сканы: их строк ядро не читает, дерева изделия нет"
    # a raster or a scan: of the document itself, or of a specification that lists it
    own = [
        path
        for path, doc in truth.documents.items()
        if doc.origin != "vector" and doc.designation in {designation, base, f"{base} СБ"}
    ]
    listing = [
        path
        for path, doc in truth.documents.items()
        if doc.kind == "specification" and doc.origin != "vector" and designation in {r.designation for r in doc.rows}
    ]
    if own or listing:
        doc = truth.documents[(own or listing)[0]]
        what = "растр" if doc.origin == "raster" else "скан"
        what += " спецификации" if doc.kind == "specification" else ""
        if use_ocr:
            return f"{what}: OCR прочитал с ошибкой"
        if own and PurePosixPath(own[0]).stem.startswith(f"{base}_"):
            return f"{what} без OCR, имя файла «обозначение_наименование»: ядро не берёт из него обозначение"
        return f"{what} без OCR: обозначение — из имени файла, остальных полей нет"
    known = {doc.designation for doc in truth.documents.values()} | {r.designation for r in rows}
    if not missed and designation not in known and base not in known:
        return "такого обозначения в комплекте нет: OCR исказил его на скане"
    if not rule.startswith("B1."):
        return "у ядра нет правила для этого типа"
    return "—"


def _group_cause(rule: str, designation: str, truth: KitTruth, *, missed: bool) -> str | None:
    """Why the core misses or adds a discrepancy around a group document of GOST 2.113, or none. A variant is covered
    by the group document of its base designation; the rows of form 5 hold the positions of variant B; in variant A
    the heading of each variant stands right below the last row of the data before it."""
    specs = [d for d in truth.documents.values() if isinstance(d, SpecTruth) and d.group]
    if any(d.group == "B" and designation in {r.designation for r in d.rows} for d in specs):
        return "позиция групповой спецификации по варианту Б (форма 5 ГОСТ 2.113): строк этой формы ядро не читает"
    if missed:
        return None
    drawn = {v.designation for d in truth.documents.values() if isinstance(d, DrawingTruth) for v in d.variants[1:]}
    if rule == "B1.SPEC.001" and any(designation in d.variants[1:] for d in specs):
        return "исполнение групповой сборочной единицы (ГОСТ 2.113): ядро ищет спецификацию с обозначением исполнения"
    if rule.startswith("B1.MISSING.") and designation in drawn:
        return "исполнение на групповом чертеже (ГОСТ 2.113): ядро ищет документ с обозначением исполнения"
    if rule == "B1.NAME.001" and any(designation in {r.designation for r in d.rows if r.variant} for d in specs):
        return "переменные данные по варианту А (ГОСТ 2.113): заголовок исполнения ниже ядро добавляет к наименованию"
    return None


def compare(report: Report, truth: KitTruth) -> dict[str, Any]:
    expected = _expected(truth)
    want = Counter(key for key, _ in expected)
    got = Counter((d.rule_id or d.type, d.designation or "") for d in report.discrepancies)
    found = want & got
    by_mutation: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    left = Counter(found)
    for key, mutation in expected:
        by_mutation[mutation or "—"][1] += 1
        if left[key]:
            left[key] -= 1
            by_mutation[mutation or "—"][0] += 1
    statuses = {d.path: d.status for d in report.documents}
    return {
        "expected": want,
        "found": found,
        "missed": want - got,
        "extra": got - want,
        "by_mutation": by_mutation,
        "files_right": sum(statuses.get(p) == s for p, s in truth.problem_files.items()),
        "files_total": len(truth.problem_files),
        "files_wrong": {p: [s, statuses.get(p)] for p, s in truth.problem_files.items() if statuses.get(p) != s},
        "other_statuses": dict(Counter(s for p, s in statuses.items() if s != "ok" and p not in truth.problem_files)),
    }


def parameters(
    report: Report, truth: KitTruth, tally: dict[str, Counter[str]], lost: Counter[str], wrong: Counter[str]
) -> None:
    """Parameters of the drawings against the truth: in ``tally`` by «<input>:<category>», what is expected, found
    and extra, and of the found ones with a number or a unit in the truth, how many have them right; in ``lost``, the
    wordings of the items of technical requirements whose parameters a vector drawing loses, digits as N; in
    ``wrong``, the values found wrong, as «category: truth → report»."""
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth):
            continue
        part = parts.get(ids.get(path, ""))
        got = list(part.parameters) if part else []
        source = "vector" if doc.origin == "vector" else "captured"
        matches = scoring.match_parameters(doc.parameters, got)
        for want, index in zip(doc.parameters, matches, strict=True):
            counts = tally[f"{source}:{want.category}"]
            counts["expected"] += 1
            if index is not None:
                counts["found"] += 1
                if scoring.has_value(want):
                    counts["valued"] += 1
                    if scoring.value_right(want, got[index]):
                        counts["value_right"] += 1
                    else:
                        wrong[f"{want.category}: {_values(want)} → {_values(got[index])}"] += 1
            elif source == "vector" and want.box is not None and want.box in doc.requirement_boxes:
                wording = re.sub(r"\d+", "N", doc.requirements[doc.requirement_boxes.index(want.box)])
                lost[f"{PARAMETER_CATEGORY.get(want.category, want.category)}: {wording}"] += 1
        for i, found in enumerate(got):
            if i not in matches:
                tally[f"{source}:{found.category}"]["extra"] += 1


TITLE_REGIONS = ("designation", "name", "material", "mass", "scale", "litera", "archive_number", "sheet", "sheets")
ROW_REGIONS = {"designation": "designation", "name": "name"}  # the column of the truth: the field of the report row
NOT_ON_SHEET = ("filename", "rule", "manual")  # methods of a value taken from elsewhere than the sheet: no region


def _boxes(doc: DrawingTruth | SpecTruth) -> list[scoring.Region]:
    """Every box of the truth of a document: its fields, its rows, its requirements and parameters."""
    boxes: list[Box | None] = [*doc.boxes.values()]
    if isinstance(doc, SpecTruth):
        boxes += [b for row in doc.rows for b in row.boxes.values()]
    else:
        boxes += [*doc.requirement_boxes, *(p.box for p in doc.parameters)]
    return [(b.page, b.bbox) for b in boxes if b is not None]


def _region(value: Extracted | Parameter | None) -> scoring.Region | None:
    src = value.source if value is not None else None
    return (src.page, src.bbox) if src is not None and src.bbox is not None else None


def regions(report: Report, truth: KitTruth, tally: dict[str, Counter[str]], wrong: Counter[str]) -> None:
    """The regions the report gives for the values of the truth that have a box: in ``tally`` by
    «<input>:<what>», how many values have a region to check and how many are right; in ``wrong``, why the others
    are not, as «what: why». A value the core did not give is not counted, its reading is measured elsewhere; nor is one
    taken from the file name, which has no region on the sheet."""
    ids = {d.path: d.id for d in report.documents}
    paths = {d.id: d.path for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    specs = {sp.document_id: sp for sp in report.specifications}
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth | SpecTruth):
            continue
        source = "vector" if doc.origin == "vector" else "captured"
        others = _boxes(doc)
        files = {n: p for p, n in doc.sheet_files.items()}  # the box of page N lies on page 1 of the file of sheet N
        pairs: list[tuple[str, Box, Extracted | Parameter]] = []
        if isinstance(doc, DrawingTruth):
            part = parts.get(ids.get(path, ""))
            if part is None:
                continue
            for field in TITLE_REGIONS:
                got = getattr(part.title_block, field)
                if field in doc.boxes and got is not None and got.value not in (None, ""):
                    pairs.append(("основная надпись", doc.boxes[field], got))
            matches = scoring.match_parameters(doc.parameters, part.parameters)
            for want, index in zip(doc.parameters, matches, strict=True):
                if want.box is not None and index is not None:
                    pairs.append(("параметр", want.box, part.parameters[index]))
        else:
            spec = specs.get(ids.get(path, ""))
            if spec is None:
                continue
            for field in ("designation", "name", "litera", "sheets"):
                got = getattr(spec, field)
                if field in doc.boxes and got is not None and got.value not in (None, ""):
                    pairs.append(("основная надпись", doc.boxes[field], got))
            rows = list(spec.rows)
            for row in doc.rows:
                key = scoring.norm(row.designation)
                index = next(
                    (
                        i
                        for i, r in enumerate(rows)
                        if key and r.designation and scoring.norm(r.designation.value) == key
                    ),
                    None,
                )
                if index is None:
                    continue
                got_row = rows.pop(index)
                for column, field in ROW_REGIONS.items():
                    got = getattr(got_row, field)
                    if column in row.boxes and got is not None and got.value not in (None, ""):
                        pairs.append(("строка спецификации", row.boxes[column], got))
        for what, box, value in pairs:
            if value is not None and value.source is not None and value.source.method in NOT_ON_SHEET:
                continue
            region = _region(value)
            counts = tally[f"{source}:{what}"]
            counts["values"] += 1
            if files:
                given = paths.get(value.source.document_id) if value.source is not None else None
                if region is not None and given != files.get(box.page):
                    wrong[f"{what}, растры и сканы: не тот файл листа"] += 1
                    continue
                local = [(1, bbox) for page, bbox in others if page == box.page]
                problem = scoring.region_problem((1, box.bbox), region, local)
            else:
                problem = scoring.region_problem((box.page, box.bbox), region, others)
            if problem is None:
                counts["right"] += 1
            else:
                wrong[f"{what}, {'вектор' if source == 'vector' else 'растры и сканы'}: {problem}"] += 1


def sheet_files(report: Report, truth: KitTruth, tally: Counter[str], wrong: list[dict[str, str]], kit: str) -> None:
    """The files of the later sheets of each document kept one sheet per file: joined to the file of its first sheet
    with the right number of the sheet, joined with another number, joined to another file, or left apart, and why."""
    docs = {d.path: d for d in report.documents}
    for first, doc in truth.documents.items():
        if len(doc.sheet_files) < 2:  # one sheet left of a document whose other file the mutation took
            continue
        tally["documents"] += 1
        head = docs.get(first)
        whole = True
        for path, number in sorted(doc.sheet_files.items(), key=lambda item: item[1]):
            if number == 1:
                continue
            tally["files"] += 1
            info = docs.get(path)
            if head is not None and info is not None and info.sheet_of == head.id:
                outcome = "right" if info.sheet == number else "wrong_number"
            elif info is not None and info.sheet_of:
                outcome = "wrong_first"
            else:
                outcome = "apart"
            tally[outcome] += 1
            if outcome != "right":
                whole = False
                why = "файла нет в отчёте" if info is None else (info.problem or f"{info.kind}, {info.status}")
                wrong.append({"kit": kit, "file": path, "outcome": outcome, "why": why})
        tally["documents_whole"] += whole


def _values(p: scoring.Wanted) -> str:
    numbers = {"=": p.value, "от": p.value_min, "до": p.value_max}
    shown = [f"{k} {_number(v)}" for k, v in numbers.items() if v is not None]
    return " ".join(shown + [p.unit or ""]).strip() or "—"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def run(truths: dict[str, KitTruth], use_ocr: bool, model: str | None = None) -> dict[str, Any]:
    settings = load_settings(use_ocr=use_ocr, use_vlm=model is not None)
    if model is not None:  # the model reads title blocks and specification rows of rasters (ADR-0019, ADR-0021)
        vlm = settings.models.vlm.model_copy(update={"model": model})
        settings = settings.model_copy(update={"models": settings.models.model_copy(update={"vlm": vlm})})
    pipeline = BaselinePipeline(settings)
    rules: dict[str, Counter[str]] = defaultdict(Counter)
    by_mutation: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    clean_false: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    files = [0, 0]
    other: Counter[str] = Counter()
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    lost: Counter[str] = Counter()
    wrong: Counter[str] = Counter()
    places: dict[str, Counter[str]] = defaultdict(Counter)
    misplaced: Counter[str] = Counter()
    sheets: Counter[str] = Counter()
    sheets_wrong: list[dict[str, str]] = []
    for name, truth in truths.items():
        report = pipeline.run(CORPUS / name, kit_id=name, kit_name=name)
        parameters(report, truth, tally, lost, wrong)
        regions(report, truth, places, misplaced)
        sheet_files(report, truth, sheets, sheets_wrong, name)
        result = compare(report, truth)
        for part in ("expected", "found", "missed", "extra"):
            for (rule, _), count in result[part].items():
                rules[rule][part] += count
        for mutation, (hit, total) in result["by_mutation"].items():
            by_mutation[mutation][0] += hit
            by_mutation[mutation][1] += total
        for key in result["missed"]:
            cause = _cause(key, truth, missed=True, use_ocr=use_ocr)
            errors.append({"kit": name, "kind": "пропуск", "rule": key[0], "designation": key[1], "cause": cause})
        for key in result["extra"]:
            cause = _cause(key, truth, missed=False, use_ocr=use_ocr)
            item = {"kit": name, "kind": "лишнее", "rule": key[0], "designation": key[1], "cause": cause}
            errors.append(item)
            if not truth.mutations:
                clean_false.append(item)
        files[0] += result["files_right"]
        files[1] += result["files_total"]
        other.update(result["other_statuses"])
        for path, (want, got) in result["files_wrong"].items():
            errors.append({"kit": name, "kind": "статус файла", "rule": want, "designation": f"{path}: {got}"})
    table = []
    for rule in sorted(rules):
        c = rules[rule]
        table.append(
            {
                "rule": rule,
                "expected": c["expected"],
                "found": c["found"],
                "missed": c["missed"],
                "extra": c["extra"],
                "precision": _ratio(c["found"], c["found"] + c["extra"]),
                "recall": _ratio(c["found"], c["expected"]),
            }
        )
    expected = sum(r["expected"] for r in table)
    found = sum(r["found"] for r in table)
    extra = sum(r["extra"] for r in table)
    return {
        "ocr": use_ocr,
        "model": model,
        "prompts": [pipeline.vlm.version, pipeline.vlm_rows.version] if pipeline.vlm and pipeline.vlm_rows else [],
        "total": {
            "expected": expected,
            "found": found,
            "extra": extra,
            "precision": _ratio(found, found + extra),
            "recall": _ratio(found, expected),
        },
        "rules": table,
        "mutations": {m: {"found": h, "expected": t} for m, (h, t) in sorted(by_mutation.items())},
        "clean_kits": sum(1 for t in truths.values() if not t.mutations),
        "clean_false": clean_false,
        "problem_files": {"right": files[0], "total": files[1]},
        "other_statuses": dict(other),
        "causes": dict(Counter(f"{e['kind']}: {e['cause']}" for e in errors if "cause" in e).most_common()),
        "errors": errors,
        "parameters": {
            key: {
                **{part: counts[part] for part in ("expected", "found", "extra", "valued", "value_right")},
                "precision": _ratio(counts["found"], counts["found"] + counts["extra"]),
                "recall": _ratio(counts["found"], counts["expected"]),
                "value_accuracy": _ratio(counts["value_right"], counts["valued"]),
            }
            for key, counts in sorted(tally.items())
        },
        "lost_requirements": dict(lost.most_common()),
        "wrong_values": dict(wrong.most_common()),
        "regions": {
            key: {**dict(counts), "share": _ratio(counts["right"], counts["values"])}
            for key, counts in sorted(places.items())
        },
        "wrong_regions": dict(misplaced.most_common()),
        "sheet_files": {**dict(sheets), "share": _ratio(sheets["right"], sheets["files"])},
        "sheet_files_wrong": sheets_wrong,
    }


def _tesseract() -> str | None:
    try:
        out = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, check=True).stdout
        return out.split()[1]
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def markdown(result: dict[str, Any]) -> str:
    corpus = result["corpus"]
    lines = [
        "# Блоки 1 и 2 на синтетических комплектах",
        "",
        f"Получено `uv run python eval/synth_kits.py --kits {corpus['kits']} --seed {corpus['seed']} "
        f"--sheet-files {corpus['sheet_files']:g}{' --vlm ' + corpus['model'] if corpus.get('model') else ''}` "
        f"{result['date']}; {result['machine']}; Tesseract {result['tesseract'] or 'нет'}. "
        f"Корпус: {corpus['kits']} комплектов из `synth.kits`, из них {corpus['clean']} без ошибок; заложено "
        f"{corpus['planted']} расхождений и {corpus['problem_files']} проблемных файлов. Из {corpus['documents']} "
        f"документов сканов {corpus['captured'].get('scan', 0)}, растров без сканирования "
        f"{corpus['captured'].get('raster', 0)}; их классы качества — в [scan-quality.md](scan-quality.md). "
        f"По листу в файле, с «(N)» в имени, лежат {corpus['sheet_documents']} документов в "
        f"{corpus['sheet_file_count']} файлах. "
        "Полностью — в [synth-kits.json](synth-kits.json), там же каждая ошибка с комплектом и обозначением.",
        "",
        "Расхождение найдено, если в отчёте есть то же правило (для типов без правила — тот же тип) с тем же "
        "обозначением. Лишнее — срабатывание, которого нет в разметке.",
    ]
    for run_ in result["runs"]:
        total = run_["total"]
        lines += [
            "",
            f"## OCR {'включён' if run_['ocr'] else 'выключен'}"
            + (f", модель {run_['model']} на основных надписях и строках спецификаций" if run_.get("model") else ""),
            "",
            f"Всего: найдено {total['found']} из {total['expected']}, лишних {total['extra']}; "
            f"точность {_number(total['precision'])}, полнота {_number(total['recall'])}. "
            f"Проблемные файлы: верный статус у {run_['problem_files']['right']} из {run_['problem_files']['total']}. "
            f"Ложных срабатываний на {run_['clean_kits']} чистых комплектах: {len(run_['clean_false'])}.",
            "",
            "| Правило | Заложено | Найдено | Пропущено | Лишних | Точность | Полнота |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in run_["rules"]:
            lines.append(
                f"| `{r['rule']}` | {r['expected']} | {r['found']} | {r['missed']} | {r['extra']} | "
                f"{_number(r['precision'])} | {_number(r['recall'])} |"
            )
        if run_["causes"]:
            lines += ["", "| Ошибка и причина | Число |", "| --- | --- |"]
            lines += [f"| {cause} | {count} |" for cause, count in run_["causes"].items()]
        lines += ["", "| Мутация | Найдено из заложенного |", "| --- | --- |"]
        for mutation, counts in run_["mutations"].items():
            lines.append(f"| `{mutation}` | {counts['found']} из {counts['expected']} |")
        if run_["other_statuses"]:
            other = ", ".join(f"{k}: {v}" for k, v in sorted(run_["other_statuses"].items()))
            lines += ["", f"Прочие непустые статусы документов, не заложенные как проблемные файлы: {other}."]
        lines += _parameters_section(run_)
        lines += _regions_section(run_)
        lines += _sheets_section(run_)
    return "\n".join(lines) + "\n"


def _sheets_section(run_: dict[str, Any]) -> list[str]:
    s = run_["sheet_files"]
    if not s.get("files"):
        return []
    lines = [
        "",
        "### Документы по листу в файле",
        "",
        "Файл последующего листа сведён верно, если ядро связало его с файлом первого листа того же документа и дало "
        "верный номер листа (`DocumentInfo.sheet_of`, `sheet`, ADR-0017).",
        "",
        "| Документов | Файлов последующих листов | Сведены верно | Другой номер листа | К другому документу "
        "| Не сведены | Документов собрано целиком |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| {s.get('documents', 0)} | {s['files']} | {s.get('right', 0)} | {s.get('wrong_number', 0)} | "
        f"{s.get('wrong_first', 0)} | {s.get('apart', 0)} | {s.get('documents_whole', 0)} |",
    ]
    why = Counter(item["why"] for item in run_["sheet_files_wrong"])
    if why:
        lines += ["", "| Почему файл не сведён | Число |", "| --- | --- |"]
        lines += [f"| {reason} | {count} |" for reason, count in why.most_common()]
    return lines


def _regions_section(run_: dict[str, Any]) -> list[str]:
    places = run_.get("regions", {})
    lines = [
        "",
        f"### Прослеживаемость: области значений, OCR {'включён' if run_['ocr'] else 'выключен'}",
        "",
        "Область, которую отчёт даёт значению, против области, где генератор нарисовал значение, — по правилу "
        "[eval/gold/README.md](../gold/README.md#что-считается-верным-извлечением): та же страница, внутри — центр "
        "области из разметки и ни одного центра других её областей того же документа. Считаются значения, которые "
        "ядро прочитало с листа, а не взяло из имени файла: поля основной надписи, обозначение и наименование строк "
        "спецификации, найденные параметры.",
        "",
        "| Что | Вектор: значений | область верна | Растры и сканы: значений | область верна |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for what in ("основная надпись", "строка спецификации", "параметр"):
        cells = []
        for source in ("vector", "captured"):
            counts = places.get(f"{source}:{what}", {})
            cells += [str(counts.get("values", 0)), str(counts.get("right", 0))]
        lines.append(f"| {what.capitalize()} | " + " | ".join(cells) + " |")
    if run_.get("wrong_regions"):
        lines += ["", "| Неверная область: что, вход, причина | Число |", "| --- | --- |"]
        lines += [f"| {why} | {count} |" for why, count in run_["wrong_regions"].items()]
    return lines


def _parameters_section(run_: dict[str, Any]) -> list[str]:
    params = run_["parameters"]
    lines = [
        "",
        f"### Блок 2: параметры чертежей, OCR {'включён' if run_['ocr'] else 'выключен'}",
        "",
        "Параметры технических требований, поля чертежа и знака общей шероховатости из разметки против параметров "
        "отчёта — по правилу [eval/gold/README.md](../gold/README.md#что-считается-верным-извлечением): один к одному "
        "по категории, имя из разметки — внутри имени, уточнения и текста найденного параметра. Лишний — параметр "
        "отчёта без пары в разметке. Значение верно, если у найденного параметра те же числа и единица, что задаёт "
        "разметка; считаются только параметры, у которых разметка их задаёт.",
        "",
        "| Категория | Вектор: ожидалось | найдено | лишних | Растры и сканы: ожидалось | найдено | лишних |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    total: dict[str, Counter[str]] = defaultdict(Counter)
    for category, title in PARAMETER_CATEGORY.items():
        cells = []
        for source in ("vector", "captured"):
            counts = params.get(f"{source}:{category}", {})
            total[source].update({k: counts.get(k, 0) for k in ("expected", "found", "extra", "valued", "value_right")})
            cells += [str(counts.get("expected", 0)), str(counts.get("found", 0)), str(counts.get("extra", 0))]
        if any(c != "0" for c in cells):
            lines.append(f"| {title} | " + " | ".join(cells) + " |")
    for source, title in (("vector", "вектор"), ("captured", "растры и сканы")):
        t = total[source]
        recall, precision = _ratio(t["found"], t["expected"]), _ratio(t["found"], t["found"] + t["extra"])
        lines.append(
            f"\nВсего, {title}: полнота {_number(recall)}, точность {_number(precision)}; значение верно у "
            f"{t['value_right']} из {t['valued']} найденных параметров с числом или единицей в разметке."
        )
    if run_["lost_requirements"]:
        lines += ["", "| Параметр, потерянный в пункте ТТ на векторе, и формулировка пункта | Число |", "| --- | --- |"]
        lines += [f"| {wording} | {count} |" for wording, count in list(run_["lost_requirements"].items())[:25]]
    if run_["wrong_values"]:
        lines += ["", "| Неверное значение: категория, разметка → отчёт | Число |", "| --- | --- |"]
        for value, count in list(run_["wrong_values"].items())[:25]:
            category, values = value.split(": ", 1)
            lines.append(f"| {PARAMETER_CATEGORY.get(category, category)}: {values} | {count} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kits", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--sheet-files", type=float, default=0.5, help="доля комплектов с растрами по листу в файле")
    parser.add_argument("--cache", action="store_true", help="брать и хранить прочтения OCR в data/cache (ADR-0018)")
    parser.add_argument("--vlm", metavar="MODEL", help="ещё прогон с OCR и этой моделью на растрах и сканах")
    args = parser.parse_args(argv)
    shutil.rmtree(CORPUS, ignore_errors=True)
    CORPUS.mkdir(parents=True)
    built = kits.corpus(CORPUS, args.kits, args.seed, sheet_files_share=args.sheet_files)
    truths = {f"kit_{i:03d}": t for i, t in enumerate(built, start=1)}
    split = [d for t in truths.values() for d in t.documents.values() if len(d.sheet_files) > 1]
    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "tesseract": _tesseract() if ocr_available() else None,
        "corpus": {
            "kits": args.kits,
            "seed": args.seed,
            "sheet_files": args.sheet_files,
            "model": args.vlm,
            "sheet_documents": len(split),
            "sheet_file_count": sum(len(d.sheet_files) for d in split),
            "clean": sum(1 for t in truths.values() if not t.mutations),
            "planted": sum(len(t.discrepancies) for t in truths.values()),
            "problem_files": sum(len(t.problem_files) for t in truths.values()),
            "documents": sum(len(t.documents) for t in truths.values()),
            "captured": dict(Counter(d.origin for t in truths.values() for d in t.documents.values())),
            "mutations": sorted(mutations.CATALOG),
        },
        "runs": [],
    }
    with read_cache.use(CACHE if args.cache else None):
        result["runs"] = [run(truths, use_ocr) for use_ocr in ([False, True] if ocr_available() else [False])]
        if args.vlm and ocr_available():
            result["runs"].append(run(truths, True, args.vlm))
    OUT.mkdir(exist_ok=True)
    (OUT / "synth-kits.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "synth-kits.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
