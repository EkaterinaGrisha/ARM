"""The Block 1 gold set on real kits (plan stages 2.4 and 8): drafts of each labeller, their agreement, the truth.

A kit is a folder of the open drawings corpus, data/open_drawings/<folder>, whose files the registry
eval/open_drawings.csv lists. Its truth is a ``synth.truth.KitTruth``, the format of the synthetic kits, so the same
code compares a report with both. Each labeller fills a draft from the documents alone, without the report of the core:
eval/gold/drafts/<folder>.<labeller>.json. Two drafts of one kit are compared value by value; the disagreements are
settled together, and the settled truth is eval/gold/<folder>.json. How to label: eval/gold/README.md.

    uv run python eval/gold.py template github/snowlue_ITMO_works --by katya
    uv run python eval/gold.py check
    uv run python eval/gold.py agree
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from itertools import combinations
from pathlib import Path
from typing import get_args

from kdcheck.schemas import DiscrepancyType
from pydantic import ValidationError
from synth.truth import DrawingTruth, KitTruth, OtherTruth, SpecTruth

EVAL = Path(__file__).resolve().parent
REGISTRY = EVAL / "open_drawings.csv"
GOLD = EVAL / "gold"
DRAFTS = GOLD / "drafts"
OUT = EVAL / "results" / "gold-agreement.md"

# Types of the report, and those the plan names for the core to find later (docs/PLAN.md, stage 4).
TYPES = (
    *get_args(DiscrepancyType),
    "format_mismatch",
    "sheets_missing",
    "section_mismatch",
    "first_use_mismatch",
    "position_anomaly",
)
NOT_DOCUMENTS = ("illustration", "text")  # a picture, a title page, a process sheet: a foreign file in the kit
TITLE = ("designation", "name", "litera", "revision", "sheets")
DRAWING = (*TITLE, "material", "mass", "scale", "archive_number", "first_use")
ROW = ("section", "fmt", "pos", "designation", "name", "qty", "note")


def read_registry() -> list[dict[str, str]]:
    with REGISTRY.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def norm(value: object) -> str:
    """Values compare after case, «ё», decimal comma and spaces, as the evaluation compares them with a report."""
    if value is None:
        return ""
    text = str(value).lower().replace("ё", "е").replace(",", ".")
    return re.sub(r"\s+", "", text)


def template(folder: str, labeller: str) -> int:
    """A draft for ``labeller``: every file of the kit with empty values, the kind taken from the registry."""
    prefix = folder.strip("/") + "/"
    rows = [row for row in read_registry() if row["path"].startswith(prefix)]
    if not rows:
        print(f"в реестре нет файлов папки {folder}", file=sys.stderr)
        return 1
    target = DRAFTS / f"{prefix.rstrip('/')}.{labeller}.json"
    if target.exists():
        print(f"черновик уже есть, его не перезаписываю: {target.relative_to(EVAL.parent)}", file=sys.stderr)
        return 1
    truth = KitTruth(root="", documents={})
    for row in rows:
        name = row["path"].removeprefix(prefix)
        kinds = row["doc_type"].split(";")
        if len(kinds) > 1:
            print(f"{name}: в одном файле несколько документов, разметка по страницам — с пунктом 4.5 плана")
            continue
        sheets = int(row["sheets"] or row["pages"])
        if kinds[0] in NOT_DOCUMENTS:
            truth.problem_files[name] = "foreign"
        elif kinds[0] == "specification":
            truth.documents[name] = SpecTruth(designation="", name="", litera="", revision=None, rows=[], sheets=sheets)
        elif kinds[0] in ("assembly", "part"):
            truth.documents[name] = DrawingTruth(
                kind="assembly_drawing" if kinds[0] == "assembly" else "part_drawing",
                origin="vector" if row["quality"] == "vector" else "scan",
                designation="",
                name="",
                material=None,
                mass=None,
                scale="",
                litera="",
                revision=None,
                archive_number=None,
                first_use=None,
                requirements=[],
                parameters=[],
                sheets=sheets,
            )
        else:
            truth.documents[name] = OtherTruth(designation="", name="", sheets=sheets)
    if not truth.documents and not truth.problem_files:
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    truth.write(target)
    print(f"черновик: {target.relative_to(EVAL.parent)}, документов {len(truth.documents)}")
    return 0


def _folder(path: Path) -> str:
    """The kit folder of a gold file: drafts/<folder>.<labeller>.json or <folder>.json."""
    if path.is_relative_to(DRAFTS):
        return path.relative_to(DRAFTS).as_posix().rsplit(".", 2)[0]
    return path.relative_to(GOLD).as_posix().removesuffix(".json")


def _drafts() -> dict[str, dict[str, KitTruth]]:
    found: dict[str, dict[str, KitTruth]] = {}
    for path in sorted(DRAFTS.rglob("*.json")):
        labeller = path.name.rsplit(".", 2)[1]
        found.setdefault(_folder(path), {})[labeller] = KitTruth.read(path)
    return found


def check() -> int:
    """Every gold file reads as a KitTruth, names files of its kit in the registry and known discrepancy types."""
    listed = {row["path"] for row in read_registry()}
    problems: list[str] = []
    paths = sorted(GOLD.rglob("*.json"))
    for path in paths:
        where = path.relative_to(EVAL.parent)
        try:
            truth = KitTruth.read(path)
        except ValidationError as exc:
            problems.append(f"{where}: {exc}")
            continue
        folder = _folder(path)
        for name in [*truth.documents, *truth.problem_files]:
            if f"{folder}/{name}" not in listed:
                problems.append(f"{where}: {name} — нет в реестре в папке {folder}")
        for d in truth.discrepancies:
            if d.type not in TYPES:
                problems.append(f"{where}: тип расхождения {d.type} не из каталога")
    print("\n".join(problems) or f"файлов эталона: {len(paths)}, ошибок нет")
    return 1 if problems else 0


def _values(truth: KitTruth) -> dict[str, str]:
    """Every labelled value by where it is: the root, the fields of each document, each specification row."""
    values = {"корень": truth.root}
    for name, doc in truth.documents.items():
        values[f"{name}: вид"] = doc.kind
        fields = DRAWING if isinstance(doc, DrawingTruth) else TITLE
        values |= {f"{name}: {field}": str(getattr(doc, field) or "") for field in fields}
        if isinstance(doc, SpecTruth):
            values[f"{name}: строк"] = str(len(doc.rows))
            for i, row in enumerate(doc.rows, start=1):
                values |= {f"{name}: строка {i}, {field}": getattr(row, field) for field in ROW}
    values |= {f"{name}: статус файла": status for name, status in truth.problem_files.items()}
    return values


def _discrepancies(truth: KitTruth) -> set[str]:
    return {f"{d.type} {d.designation or d.name or ''}".strip() for d in truth.discrepancies}


def compare(a: KitTruth, b: KitTruth) -> tuple[int, int, list[tuple[str, str, str]]]:
    """Agreed values, values either labeller filled, and the disagreements as (where, value of a, value of b)."""
    va, vb = _values(a), _values(b)
    keys = sorted(k for k in va.keys() | vb.keys() if norm(va.get(k)) or norm(vb.get(k)))
    differ = [(k, va.get(k, "—"), vb.get(k, "—")) for k in keys if norm(va.get(k)) != norm(vb.get(k))]
    da, db = _discrepancies(a), _discrepancies(b)
    differ += [("расхождение", d, "—") for d in sorted(da - db)] + [("расхождение", "—", d) for d in sorted(db - da)]
    total = len(keys) + len(da | db)
    return total - len(differ), total, differ


def agree() -> int:
    """Compare the drafts of each kit that two labellers filled; write the result to eval/results/gold-agreement.md."""
    lines = [
        "# Эталон Блока 1: согласие разметчиков",
        "",
        "Скрипт `eval/gold.py agree`: два черновика одного комплекта сравниваются значение за значением после",
        "регистра, «ё», десятичной запятой и пробелов, как сравнивает оценка. Расхождения разбирают вместе,",
        "решения записывают в протокол [eval/gold/README.md](../gold/README.md).",
        "",
    ]
    pairs = 0
    for folder, drafts in _drafts().items():
        for (wa, a), (wb, b) in combinations(sorted(drafts.items()), 2):
            pairs += 1
            agreed, total, differ = compare(a, b)
            share = f"{agreed / total:.2f}".replace(".", ",") if total else "—"
            lines += [f"## {folder}: {wa} и {wb}", "", f"Совпало {agreed} значений из {total}, доля {share}.", ""]
            if differ:
                lines += [f"| Где | {wa} | {wb} |", "| --- | --- | --- |"]
                lines += [f"| {k} | {x or '«»'} | {y or '«»'} |" for k, x, y in differ]
                lines.append("")
    if not pairs:
        print("нет комплекта с двумя черновиками", file=sys.stderr)
        return 1
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("template", help="черновик разметки комплекта")
    p.add_argument("folder", help="папка комплекта в data/open_drawings, например github/snowlue_ITMO_works")
    p.add_argument("--by", required=True, help="кто размечает: katya, anya")
    sub.add_parser("check", help="формат файлов эталона и пути по реестру")
    sub.add_parser("agree", help="согласие двух черновиков одного комплекта")
    args = parser.parse_args(argv)
    if args.command == "template":
        return template(args.folder, args.by)
    return check() if args.command == "check" else agree()


if __name__ == "__main__":
    raise SystemExit(main())
