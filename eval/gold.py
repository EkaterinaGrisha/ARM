"""The gold set on real documents (plan stages 2.4 and 8): drafts of each labelling, their agreement, the truth.

A kit is a folder of the open drawings corpus, data/open_drawings/<folder>, whose files the registry
eval/open_drawings.csv lists. The sheets are a second set, ``sheets``: eval/gold/sheets.csv takes single sheets with a
title block out of the corpus documents, as a one-page PDF or as a picture, and the ``sheets`` command cuts them into
data/gold_sheets (outside git). The truth of either is a ``synth.truth.KitTruth``, the format of the synthetic kits, so
the same code compares a report with both. Each labelling is a draft made from the documents alone, without the report
of the core: eval/gold/drafts/<folder>.<label>.json, the label a short tag such as ``a``. Two drafts are compared value
by value on the documents both hold, each disagreement is settled by the sheet, and the settled truth is
eval/gold/<folder>.json. The labelling rules: eval/gold/README.md.

    uv run python eval/gold.py template github/snowlue_ITMO_works --by a
    uv run python eval/gold.py sheets
    uv run python eval/gold.py template sheets --by a --only vl60_p025.pdf izh250_p002.jpg
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

import pypdfium2 as pdfium
from kdcheck.config import load_settings
from kdcheck.schemas import DiscrepancyType
from pydantic import ValidationError
from synth.truth import DrawingTruth, KitTruth, OtherTruth, SpecTruth

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
REGISTRY = EVAL / "open_drawings.csv"
CORPUS = ROOT / "data" / "open_drawings"
GOLD = EVAL / "gold"
DRAFTS = GOLD / "drafts"
SHEETS = GOLD / "sheets.csv"
SHEET_DIR = ROOT / "data" / "gold_sheets"
SHEET_DPI = 300  # a sheet given as a picture is rendered as an office scanner would scan it
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
# What looks the same on the sheet but differs as typed: Latin letters like Cyrillic ones (configs/dictionaries.yaml),
# dashes and quotes
FOLD = str.maketrans(
    load_settings().dictionaries.latin_lookalikes | dict.fromkeys("‐‑‒–—−", "-") | dict.fromkeys("«»“”„", '"')
)


def read_registry() -> list[dict[str, str]]:
    with REGISTRY.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_sheets() -> list[dict[str, str]]:
    """The sheets of the gold set: file name, source document in the registry, page from 1, form, kind."""
    with SHEETS.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def norm(value: object) -> str:
    """Values compare after case, «ё», decimal comma, spaces, Latin letters like Cyrillic ones, dashes and quotes."""
    if value is None:
        return ""
    text = str(value).translate(FOLD).lower().replace("ё", "е").replace(",", ".")
    return re.sub(r"\s+", "", text)


def _listed(folder: str) -> list[tuple[str, str, str, int]]:
    """Files of a kit folder in the registry, or the sheets of the gold set: name, kind, quality, sheets."""
    registry = read_registry()
    if folder == "sheets":
        quality = {row["path"]: row["quality"] for row in registry}
        return [(row["sheet"], row["kind"], quality.get(row["source"], ""), 1) for row in read_sheets()]
    prefix = folder + "/"
    return [
        (row["path"].removeprefix(prefix), row["doc_type"], row["quality"], int(row["sheets"] or row["pages"]))
        for row in registry
        if row["path"].startswith(prefix)
    ]


def template(folder: str, labeller: str, only: list[str] | None = None) -> int:
    """A draft for ``labeller``: every file of the kit, or those in ``only``, with empty values and the kind given."""
    folder = folder.strip("/")
    rows = [row for row in _listed(folder) if not only or row[0] in only]
    if not rows:
        print(f"нет файлов папки {folder}", file=sys.stderr)
        return 1
    target = DRAFTS / f"{folder}.{labeller}.json"
    if target.exists():
        print(f"черновик уже есть, его не перезаписываю: {target.relative_to(ROOT)}", file=sys.stderr)
        return 1
    truth = KitTruth(root="", documents={})
    for name, doc_type, quality, sheets in rows:
        kinds = doc_type.split(";")
        if len(kinds) > 1:
            print(f"{name}: в одном файле несколько документов, разметка по страницам — с пунктом 4.5 плана")
            continue
        if kinds[0] in NOT_DOCUMENTS:
            truth.problem_files[name] = "foreign"
        elif kinds[0] == "specification":
            truth.documents[name] = SpecTruth(designation="", name="", litera="", revision=None, rows=[], sheets=sheets)
        elif kinds[0] in ("assembly", "part"):
            truth.documents[name] = DrawingTruth(
                kind="assembly_drawing" if kinds[0] == "assembly" else "part_drawing",
                origin="vector" if quality == "vector" else "scan",
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
    print(f"черновик: {target.relative_to(ROOT)}, документов {len(truth.documents)}")
    return 0


def sheets() -> int:
    """Cut the sheets of eval/gold/sheets.csv out of the corpus into data/gold_sheets.

    Form ``pdf`` keeps the page as it is, with its text layer; form ``jpg`` renders it into a picture with no text.
    """
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    for row in read_sheets():
        source = CORPUS / row["source"]
        if not source.exists():
            problems.append(f"{row['sheet']}: нет {source.relative_to(ROOT)}, скачивает eval/open_drawings.py fetch")
            continue
        pdf = pdfium.PdfDocument(source)
        index = int(row["page"]) - 1
        target = SHEET_DIR / row["sheet"]
        if row["form"] == "pdf":
            page = pdfium.PdfDocument.new()
            page.import_pages(pdf, [index])
            page.save(target)
        else:
            picture = pdf[index].render(scale=SHEET_DPI / 72).to_pil().convert("RGB")
            picture.save(target, "JPEG", quality=90, dpi=(SHEET_DPI, SHEET_DPI))
    print("\n".join(problems) or f"листов: {len(read_sheets())}, папка {SHEET_DIR.relative_to(ROOT)}")
    return 1 if problems else 0


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
    """Every gold file reads as a KitTruth and names known files and discrepancy types; sheets come from the corpus."""
    registry = {row["path"] for row in read_registry()}
    listed = registry | {f"sheets/{row['sheet']}" for row in read_sheets()}
    problems: list[str] = []
    names = [row["sheet"] for row in read_sheets()]
    problems += [f"{SHEETS.name}: лист {n} назван дважды" for n in sorted({n for n in names if names.count(n) > 1})]
    for row in read_sheets():
        if row["source"] not in registry:
            problems.append(f"{SHEETS.name}: {row['sheet']} — источника {row['source']} нет в реестре")
        if row["form"] not in ("pdf", "jpg") or row["kind"] not in ("part", "assembly"):
            problems.append(f"{SHEETS.name}: {row['sheet']} — форма {row['form']}, вид {row['kind']}")
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
                problems.append(f"{where}: {name} — нет ни в реестре в папке {folder}, ни в {SHEETS.name}")
        for d in truth.discrepancies:
            if d.type not in TYPES:
                problems.append(f"{where}: тип расхождения {d.type} не из каталога")
    print("\n".join(problems) or f"файлов эталона: {len(paths)}, ошибок нет")
    return 1 if problems else 0


def _values(truth: KitTruth, names: set[str]) -> dict[str, str]:
    """Every labelled value by where it is: the root, the fields of the documents named, each specification row."""
    values = {"корень": truth.root}
    for name, doc in truth.documents.items():
        if name not in names:
            continue
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
    """Agreed values, values either labelling filled, and the disagreements as (where, value of a, value of b).

    Only the documents both drafts hold are compared: a set of sheets may be labelled twice in part.
    """
    common = a.documents.keys() & b.documents.keys()
    va, vb = _values(a, common), _values(b, common)
    keys = sorted(k for k in va.keys() | vb.keys() if norm(va.get(k)) or norm(vb.get(k)))
    differ = [(k, va.get(k, "—"), vb.get(k, "—")) for k in keys if norm(va.get(k)) != norm(vb.get(k))]
    da, db = _discrepancies(a), _discrepancies(b)
    differ += [("расхождение", d, "—") for d in sorted(da - db)] + [("расхождение", "—", d) for d in sorted(db - da)]
    total = len(keys) + len(da | db)
    return total - len(differ), total, differ


def agree() -> int:
    """Compare the two drafts of each kit or set labelled twice; write the result to eval/results/gold-agreement.md."""
    lines = [
        "# Эталон: согласие разметок",
        "",
        "Скрипт `eval/gold.py agree`: два черновика одного комплекта или набора листов сравниваются значение",
        "за значением на документах, которые есть в обоих, без учёта регистра, «ё», десятичной запятой, пробелов,",
        "тире, кавычек и латинских букв, похожих на русские. Решение по каждому несовпадению — в протоколе",
        "[eval/gold/README.md](../gold/README.md).",
        "",
    ]
    pairs = 0
    for folder, drafts in _drafts().items():
        for (wa, a), (wb, b) in combinations(sorted(drafts.items()), 2):
            pairs += 1
            agreed, total, differ = compare(a, b)
            share = f"{agreed / total:.2f}".replace(".", ",") if total else "—"
            both = len(a.documents.keys() & b.documents.keys())
            lines += [
                f"## {folder}: {wa} и {wb}",
                "",
                f"Документов в обеих разметках: {both}. Совпало значений: {agreed} из {total}, доля {share}.",
                "",
            ]
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
    p = sub.add_parser("template", help="черновик разметки комплекта или листов")
    p.add_argument("folder", help="папка комплекта в data/open_drawings, например github/snowlue_ITMO_works; sheets")
    p.add_argument("--by", required=True, help="метка разметки в имени черновика: a, b")
    p.add_argument("--only", nargs="+", help="только эти файлы или листы")
    sub.add_parser("sheets", help="вырезать листы эталона из корпуса в data/gold_sheets")
    sub.add_parser("check", help="формат файлов эталона, пути по реестру и список листов")
    sub.add_parser("agree", help="согласие двух черновиков одного комплекта или набора листов")
    args = parser.parse_args(argv)
    if args.command == "template":
        return template(args.folder, args.by, args.only)
    if args.command == "sheets":
        return sheets()
    return check() if args.command == "check" else agree()


if __name__ == "__main__":
    raise SystemExit(main())
