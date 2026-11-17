"""The gold set on real documents (plan stages 2.4 and 8): drafts of each labelling, their agreement, the truth.

A kit is a folder of the open drawings corpus, data/open_drawings/<folder>, whose files the registry
eval/open_drawings.csv lists. The sheets are a second set, ``sheets``: eval/gold/sheets.csv takes single sheets with a
title block out of the corpus documents, as a one-page PDF or as a picture, and the ``sheets`` command cuts them into
data/gold_sheets (outside git). The truth of either is a ``synth.truth.KitTruth``, the format of the synthetic kits, so
the same code compares a report with both. Each labelling is a draft made from the documents alone, without the report
of the core: eval/gold/drafts/<folder>.<label>.json, the label a short tag such as ``a``; a kit of the customer has its
drafts in data/arm/gold/<kit>.<label>.json, outside git. Two drafts are compared value by value on the documents both
hold, each disagreement is settled by the sheet, and the settled truth is eval/gold/<folder>.json or
data/arm/gold/<kit>.json. The labelling rules: eval/gold/README.md. ``kits`` runs the core on every kit with a settled
truth and compares its discrepancies with the truth by type and designation: the labels name types, not rules.

    uv run python eval/gold.py template github/snowlue_ITMO_works --by a
    uv run python eval/gold.py sheets
    uv run python eval/gold.py template sheets --by a --only vl60_p025.pdf izh250_p002.jpg
    uv run python eval/gold.py check
    uv run python eval/gold.py agree
    uv run python eval/gold.py kits
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from datetime import UTC, datetime
from difflib import SequenceMatcher
from itertools import combinations, zip_longest
from pathlib import Path
from typing import Any, TypeVar, get_args

import pypdfium2 as pdfium
from kdcheck.config import load_settings
from kdcheck.normalize.designations import Designations
from kdcheck.pipeline import BaselinePipeline
from kdcheck.run import ocr_available
from kdcheck.schemas import DiscrepancyType, ParameterCategory
from pydantic import ValidationError
from scoring import norm, region_right
from synth.truth import Box, DocumentTruth, DrawingTruth, KitTruth, OtherTruth, ParameterTruth, SpecTruth

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
ARM = ROOT / "data" / "arm"  # the customer's kits and their labellings, outside git (eval/arm_kits.py)
SAMPLES = "samples.json"  # in ARM: by kit, the documents whose contents both labellings hold

# Types of the report, and those the plan names for the core to find later (docs/PLAN.md, stage 4).
TYPES = (
    *get_args(DiscrepancyType),
    "format_mismatch",
    "section_mismatch",
    "first_use_mismatch",
    "position_anomaly",
)
NOT_DOCUMENTS = ("illustration", "text")  # a picture, a title page, a process sheet: a foreign file in the kit
TITLE = ("designation", "name", "litera", "revision", "sheets")
DRAWING = (*TITLE, "material", "mass", "scale", "archive_number", "first_use")
ROW = ("section", "fmt", "pos", "designation", "name", "qty", "note", "variant", "written")
VARIANT = ("designation", "written", "mass")
PARAMETER = ("name", "value", "value_min", "value_max", "unit", "it_grade", "box")
PARTS = ("документы", "строки спецификаций", "пункты ТТ", "параметры", "области", "расхождения")
KINDS = {"specification": "specification", "assembly": "assembly_drawing", "part": "part_drawing"}  # registry → truth
GRADED = ("dimension_tolerance", "general_tolerance")  # the categories whose tolerance has an IT grade
T = TypeVar("T")
Compared = tuple[str, str, str, str, bool]  # part, where, the value of each labelling as shown, whether they agree


def read_registry() -> list[dict[str, str]]:
    with REGISTRY.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_sheets() -> list[dict[str, str]]:
    """The sheets of the gold set: file name, source document in the registry, page from 1, form, kind."""
    with SHEETS.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


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


def blank(kind: str, origin: str, sheets: int = 1) -> SpecTruth | DrawingTruth | OtherTruth:
    """A document of ``kind`` with every value empty, for a labeller to fill; a cell the blank has not is the
    labeller's None."""
    if kind == "specification":
        return SpecTruth(designation="", name="", litera="", revision="", rows=[], sheets=sheets, origin=origin)
    if kind == "other":
        return OtherTruth(designation="", name="", revision="", sheets=sheets, origin=origin)
    empty = dict.fromkeys(DRAWING, "") | {"sheets": sheets}
    return DrawingTruth(kind=kind, origin=origin, requirements=[], parameters=[], **empty)


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
        else:
            kind = KINDS.get(kinds[0], "other")
            truth.documents[name] = blank(kind, "vector" if quality == "vector" else "scan", sheets)
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


def _drafts(where: Path) -> dict[str, dict[str, KitTruth]]:
    """The drafts under ``where`` by kit and label, <kit>.<label>.json; a settled truth, <kit>.json, is no draft."""
    found: dict[str, dict[str, KitTruth]] = {}
    for path in sorted(where.rglob("*.json")):
        kit, dot, label = path.relative_to(where).as_posix().removesuffix(".json").rpartition(".")
        if dot:
            found.setdefault(kit, {})[label] = KitTruth.read(path)
    return found


def _files(truth: KitTruth) -> list[str]:
    """Every file a truth names: documents, later sheets of a document kept one sheet per file, problem files."""
    sheets = [name for doc in truth.documents.values() for name in doc.sheet_files]
    return sorted({*truth.documents, *sheets, *truth.problem_files})


def _box_problem(box: Box, sheets: int) -> str | None:
    x0, y0, x1, y1 = box.bbox
    if not 1 <= box.page <= sheets:
        return f"область на листе {box.page}, листов {sheets}"
    if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
        return f"область {box.bbox} не внутри листа"
    return None


def content_problems(truth: KitTruth) -> list[str]:
    """Discrepancy types out of the catalog, and what is wrong with the requirements and parameters of the drawings:
    a category the report has not, an IT grade of a parameter that has no grade, a region outside the document."""
    problems = [f"тип расхождения {d.type} не из каталога" for d in truth.discrepancies if d.type not in TYPES]
    categories = get_args(ParameterCategory)
    for name, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth):
            continue
        items, boxed = len(doc.requirements), len(doc.requirement_boxes)
        if boxed and boxed != items:
            problems.append(f"{name}: областей пунктов ТТ {boxed}, пунктов {items}")
        boxes = [b for b in doc.requirement_boxes if b is not None] + [p.box for p in doc.parameters if p.box]
        problems += [f"{name}: {why}" for why in (_box_problem(b, doc.sheets) for b in boxes) if why]
        for p in doc.parameters:
            if p.category not in categories:
                problems.append(f"{name}: категория параметра {p.category} не из отчёта")
            if p.it_grade is not None and (p.category not in GRADED or not 1 <= p.it_grade <= 18):
                problems.append(f"{name}: квалитет IT{p.it_grade} у параметра {p.category} «{p.name}»")
    return problems


def check() -> int:
    """Every gold file reads as a KitTruth and names known files and discrepancy types; sheets come from the corpus.

    The labellings of the customer's kits, data/arm/gold/<kit>[.<label>].json, name the files of their kit in the
    manifest of eval/arm_kits.py; they are checked where data/arm is.
    """
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
    arm = sorted((ARM / "gold").glob("*.json"))
    manifest = json.loads((ARM / "manifest.json").read_text(encoding="utf-8")) if arm else {}
    for path in paths + arm:
        where = path.relative_to(ARM.parent.parent if path in arm else ROOT)
        try:
            truth = KitTruth.read(path)
        except ValidationError as exc:
            problems.append(f"{where}: {exc}")
            continue
        if path in arm:
            kit = path.name.split(".")[0]
            unknown = [name for name in _files(truth) if name not in manifest.get(kit, {})]
            problems += [f"{where}: {name} — нет в манифесте комплекта {kit}" for name in unknown]
        else:
            folder = _folder(path)
            for name in [*truth.documents, *truth.problem_files]:
                if f"{folder}/{name}" not in listed:
                    problems.append(f"{where}: {name} — нет ни в реестре в папке {folder}, ни в {SHEETS.name}")
        problems += [f"{where}: {why}" for why in content_problems(truth)]
    done = f"файлов эталона: {len(paths) + len(arm)}, из них комплектов АРМ {len(arm)}; ошибок нет"
    print("\n".join(problems) or done)
    return 1 if problems else 0


def _shown(value: object, labelled: bool) -> str:
    if not labelled:
        return "—"
    if isinstance(value, Box):
        return f"лист {value.page}: " + " ".join(f"{v:.3f}" for v in value.bbox)
    return "«»" if value is None or value == "" else str(value)


def _same_region(x: Box, y: Box, xs: Sequence[Box], ys: Sequence[Box]) -> bool:
    """Whether two labellings put a value in one region, by the rule of scoring.py both ways: each region holds the
    centre of the other and of no other region the other labelling gives in the document."""
    rx, ry = (x.page, x.bbox), (y.page, y.bbox)
    return region_right(rx, ry, [(b.page, b.bbox) for b in xs]) and region_right(ry, rx, [(b.page, b.bbox) for b in ys])


def _compared(
    part: str,
    where: str,
    x: dict[str, Any] | None,
    y: dict[str, Any] | None,
    regions: tuple[Sequence[Box], Sequence[Box]] = ((), ()),
) -> list[Compared]:
    """One labelled thing in two labellings, field by field, the fields filled in either; ``x`` or ``y`` is None when
    one labelling has no such thing. A field that holds a region counts in the part «области»."""
    out: list[Compared] = []
    for field in dict.fromkeys([*(x or {}), *(y or {})]):
        vx, vy = (None if d is None else d.get(field) for d in (x, y))
        boxed = isinstance(vx, Box) or isinstance(vy, Box)
        if boxed:
            same = isinstance(vx, Box) and isinstance(vy, Box) and _same_region(vx, vy, *regions)
        elif norm(vx) or norm(vy):
            same = norm(vx) == norm(vy)
        else:
            continue
        shown = _shown(vx, x is not None), _shown(vy, y is not None)
        out.append(("области" if boxed else part, f"{where} {field}".strip(), *shown, same))
    return out


def _number(i: int | None, j: int | None) -> str:
    """The number of an item, «4», or its numbers in each labelling where they differ, «4/5», «—/5»."""
    return str(i + 1) if i == j and i is not None else "/".join("—" if k is None else str(k + 1) for k in (i, j))


def _align(xs: Sequence[T], ys: Sequence[T], key: Callable[[T], str]) -> list[tuple[str, T | None, T | None]]:
    """Two labelled lists item to item, with the number of each pair: the runs of equal keys difflib finds, the items
    between two runs one to one in order, the rest in one list only. A row one labelling has not shifts no other."""
    pairs: list[tuple[str, T | None, T | None]] = []
    matcher = SequenceMatcher(None, [key(x) for x in xs], [key(y) for y in ys], autojunk=False)
    for _, i0, i1, j0, j1 in matcher.get_opcodes():
        for k in range(max(i1 - i0, j1 - j0)):
            i, j = (i0 + k if i0 + k < i1 else None), (j0 + k if j0 + k < j1 else None)
            pairs.append((_number(i, j), None if i is None else xs[i], None if j is None else ys[j]))
    return pairs


def _pair_parameters(
    xs: list[ParameterTruth], ys: list[ParameterTruth], regions: tuple[Sequence[Box], Sequence[Box]]
) -> list[tuple[str, ParameterTruth | None, ParameterTruth | None]]:
    """The parameters of a drawing in two labellings one to one, with the number of each pair: the same category and
    name in one region first, then the same category and name in order, then the same category in one region, a name
    read two ways. The rest are in one labelling only."""

    def named(x: ParameterTruth, y: ParameterTruth) -> bool:
        return x.category == y.category and norm(x.name) == norm(y.name)

    def placed(x: ParameterTruth, y: ParameterTruth) -> bool:
        return (
            x.category == y.category
            and x.box is not None
            and y.box is not None
            and _same_region(x.box, y.box, *regions)
        )

    pairs: dict[int, int] = {}
    for test in (lambda x, y: named(x, y) and placed(x, y), named, placed):
        for i, x in enumerate(xs):
            if i in pairs:
                continue
            j = next((j for j, y in enumerate(ys) if j not in pairs.values() and test(x, y)), None)
            if j is not None:
                pairs[i] = j
    alone = [j for j in range(len(ys)) if j not in pairs.values()]
    return [(_number(i, pairs.get(i)), x, ys[pairs[i]] if i in pairs else None) for i, x in enumerate(xs)] + [
        (_number(None, j), None, ys[j]) for j in alone
    ]


def _drawing(name: str, x: DocumentTruth, y: DocumentTruth) -> list[Compared]:
    """The table of variants, the items of the technical requirements and the parameters of a drawing in two
    labellings; a document one labelling takes for no drawing has none of them there."""
    out: list[Compared] = []
    variants = [d.variants if isinstance(d, DrawingTruth) else [] for d in (x, y)]
    for number, vx, vy in _align(*variants, key=lambda v: norm(v.designation)):
        fx, fy = (
            None if v is None else {f: getattr(v, f) for f in VARIANT} | {f"data {c}": s for c, s in v.data.items()}
            for v in (vx, vy)
        )
        out += _compared("документы", f"{name}: исполнение {number},", fx, fy)
    items = [
        list(zip_longest(d.requirements, d.requirement_boxes)) if isinstance(d, DrawingTruth) else [] for d in (x, y)
    ]
    params = [d.parameters if isinstance(d, DrawingTruth) else [] for d in (x, y)]
    regions = (
        [box for _, box in items[0] if box] + [p.box for p in params[0] if p.box],
        [box for _, box in items[1] if box] + [p.box for p in params[1] if p.box],
    )
    for number, ix, iy in _align(*items, key=lambda item: norm(item[0])):
        fx, fy = (None if item is None else {"text": item[0], "box": item[1]} for item in (ix, iy))
        out += _compared("пункты ТТ", f"{name}: пункт ТТ {number},", fx, fy, regions)
    for number, px, py in _pair_parameters(*params, regions):
        fx, fy = (None if p is None else {f: getattr(p, f) for f in PARAMETER} for p in (px, py))
        category = (px or py).category
        out += _compared("параметры", f"{name}: параметр {number} {category},", fx, fy, regions)
    return out


def compare(
    a: KitTruth, b: KitTruth, contents: Collection[str] | None = None, discrepancies: bool = True
) -> list[Compared]:
    """Every value filled in either labelling: its part, where it is, the value of each, whether they agree.

    Only the documents both drafts hold are compared: a set of sheets may be labelled twice in part. The rows, items,
    parameters and variants of a document count only for the documents in ``contents``, when a sample of them was
    labelled twice, and the discrepancies only with ``discrepancies``. Specification rows, items of the technical
    requirements and variants are paired by :func:`_align`, parameters by :func:`_pair_parameters`; regions agree by
    :func:`_same_region`.
    """
    out = _compared("документы", "", {"корень": a.root}, {"корень": b.root})
    for name in sorted(a.documents.keys() & b.documents.keys()):
        x, y = a.documents[name], b.documents[name]
        fx, fy = (
            {"вид": d.kind}
            | {f: getattr(d, f) for f in (DRAWING if isinstance(d, DrawingTruth) else TITLE)}
            | {f"лист файла {file}": sheet for file, sheet in d.sheet_files.items()}
            for d in (x, y)
        )
        out += _compared("документы", f"{name}:", fx, fy)
        if contents is not None and name not in contents:
            continue
        fx, fy = (
            {"group": d.group, "variants": " ".join(d.variants), "строк": len(d.rows)}
            if isinstance(d, SpecTruth)
            else {}
            for d in (x, y)
        )
        out += _compared("строки спецификаций", f"{name}:", fx, fy)
        rows = [d.rows if isinstance(d, SpecTruth) else [] for d in (x, y)]
        for number, rx, ry in _align(*rows, key=lambda r: norm(r.designation + r.name)):
            fx, fy = (
                None
                if r is None
                else {f: getattr(r, f) for f in ROW} | {f"qty {v}": q for v, q in r.quantities.items()}
                for r in (rx, ry)
            )
            out += _compared("строки спецификаций", f"{name}: строка {number},", fx, fy)
        out += _drawing(name, x, y)
    for name in sorted(a.problem_files.keys() | b.problem_files.keys()):
        out += _compared("документы", f"{name}:", *({"статус файла": t.problem_files.get(name)} for t in (a, b)))
    if not discrepancies:
        return out
    da, db = ({f"{d.type} {d.designation or d.name or ''}".strip() for d in t.discrepancies} for t in (a, b))
    for d in sorted(da | db):
        out += _compared("расхождения", "расхождение", {"": d} if d in da else None, {"": d} if d in db else None)
    return out


def agree() -> int:
    """Compare the two drafts of each kit or set labelled twice; write the result to eval/results/gold-agreement.md.

    A kit of the customer gives its numbers there, and its disagreements go next to its drafts in data/arm/gold,
    <kit>.<a>-<b>.md, outside git: they name its documents. When data/arm/samples.json gives a sample of the kit, the
    contents of the documents count on the sample only.
    """

    def share(agreed: int, total: int) -> str:
        return f"{agreed / total:.2f}".replace(".", ",") if total else "—"

    lines = [
        "# Эталон: согласие разметок",
        "",
        "Скрипт `eval/gold.py agree`: два черновика одного комплекта или набора листов сравниваются значение",
        "за значением на документах, которые есть в обоих, без учёта регистра, «ё», десятичной запятой, пробелов,",
        "тире, кавычек и латинских букв, похожих на русские. Строки спецификаций и пункты технических требований",
        "сопоставляются по порядку, и строка, которой нет в одной разметке, не сдвигает остальные; параметры —",
        "по категории и имени; области — по центру, как в [scoring.py](../scoring.py). Решение по каждому",
        "несовпадению — в протоколе [eval/gold/README.md](../gold/README.md); несовпадения комплектов АРМ — рядом",
        "с их разметкой в `data/arm/gold`, вне git, здесь — только числа.",
        "",
    ]
    pairs = 0
    samples = json.loads((ARM / SAMPLES).read_text(encoding="utf-8")) if (ARM / SAMPLES).exists() else {}
    drafts = [(kit, d, False) for kit, d in _drafts(DRAFTS).items()]
    drafts += [(kit, d, True) for kit, d in _drafts(ARM / "gold").items()]
    for kit, labelled, arm in drafts:
        for (wa, a), (wb, b) in combinations(sorted(labelled.items()), 2):
            pairs += 1
            sample = samples.get(kit) if arm else None
            if sample:
                compared = compare(a, b, set(sample["documents"]), sample["discrepancies"])
            else:
                compared = compare(a, b)
            agreed = sum(same for *_, same in compared)
            both = len(a.documents.keys() & b.documents.keys())
            lines += [
                f"## {kit}: {wa} и {wb}",
                "",
                f"Документов в обеих разметках: {both}. Совпало значений: {agreed} из {len(compared)}, "
                f"доля {share(agreed, len(compared))}.",
                "",
                "| Что | Совпало | Всего | Доля |",
                "| --- | ---: | ---: | ---: |",
            ]
            for part in PARTS:
                of = [same for p, *_, same in compared if p == part]
                if of:
                    lines.append(f"| {part} | {sum(of)} | {len(of)} | {share(sum(of), len(of))} |")
            lines.append("")
            if sample:
                lines += [
                    f"Основные надписи — у всех документов; строки, пункты ТТ, параметры и исполнения — у выборки из "
                    f"{len(sample['documents'])} документов, по видам, случайно; расхождения "
                    f"{'размечены дважды' if sample['discrepancies'] else 'дважды не размечены'}.",
                    "",
                ]
            differ = [f"| {where} | {x} | {y} |" for _, where, x, y, same in compared if not same]
            if not differ:
                continue
            table = [f"| Где | {wa} | {wb} |", "| --- | --- | --- |", *differ, ""]
            if arm:
                target = ARM / "gold" / f"{kit}.{wa}-{wb}.md"
                target.write_text("\n".join([f"# {kit}: несовпадения {wa} и {wb}", "", *table]), encoding="utf-8")
                lines += [f"Несовпадений {len(differ)}, список — `data/arm/gold/{target.name}`.", ""]
            else:
                lines += table
    if not pairs:
        print("нет комплекта с двумя черновиками", file=sys.stderr)
        return 1
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def kits() -> int:
    """Block 1 on the kits with a settled truth: found, missed and extra discrepancies by type and designation."""
    settings = load_settings()
    eskd = Designations(settings.dictionaries)
    pipeline = BaselinePipeline(settings)

    def key(kind: str, designation: str | None) -> tuple[str, str]:
        return kind, eskd.normalize_designation(designation) or (designation or "").strip()

    measured: dict[str, dict[str, Any]] = {}
    for path in sorted(GOLD.glob("github/*.json")):
        folder = _folder(path)
        truth = KitTruth.read(path)
        report = pipeline.run(CORPUS / folder, kit_id=folder, kit_name=folder)
        want = Counter(key(d.type, d.designation) for d in truth.discrepancies)
        got = Counter(key(d.type, d.designation) for d in report.discrepancies)
        measured[folder] = {
            "expected": sum(want.values()),
            "found": sum((want & got).values()),
            "missed": sorted(f"{t}: {d}" for t, d in (want - got).elements()),
            "extra": sorted(f"{t}: {d}" for t, d in (got - want).elements()),
            "tree_items": report.summary.tree_items,
        }
    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "ocr": ocr_available(settings),
        "kits": measured,
    }
    lines = [
        "# Блок 1 на размеченных комплектах",
        "",
        f"Получено `uv run python eval/gold.py kits` {result['date']}; {result['machine']}; "
        f"OCR {'включён' if result['ocr'] else 'выключен'}. Комплекты — учебные с GitHub из корпуса открытых "
        "чертежей, разметка — [eval/gold/github](../gold/github). В разметке — типы расхождений, а не правила ядра, "
        "поэтому расхождение найдено, если в отчёте есть тот же тип с тем же обозначением после нормализации. "
        "Пропуски и лишние по комплектам — в [gold-kits.json](gold-kits.json).",
        "",
        "| Комплект | Элементов дерева | В разметке | Найдено | Пропущено | Лишних | Лишние по типам |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for folder, k in measured.items():
        extra = Counter(e.split(":")[0] for e in k["extra"])
        by_type = ", ".join(f"`{t}` {n}" for t, n in sorted(extra.items())) or "—"
        lines.append(
            f"| {folder} | {k['tree_items']} | {k['expected']} | {k['found']} | {len(k['missed'])} "
            f"| {len(k['extra'])} | {by_type} |"
        )
    out = EVAL / "results" / "gold-kits"
    out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    sub.add_parser("kits", help="Блок 1 на комплектах с согласованной разметкой: ядро против разметки")
    args = parser.parse_args(argv)
    if args.command == "template":
        return template(args.folder, args.by, args.only)
    if args.command == "sheets":
        return sheets()
    if args.command == "kits":
        return kits()
    return check() if args.command == "check" else agree()


if __name__ == "__main__":
    raise SystemExit(main())
