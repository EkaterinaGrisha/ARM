"""The customer's kits: a local register with the sha256 of every file, and the core's run on them, in counts only.

The kits are customer data. They stay in data/arm outside git, and so do the register that names their folders
(data/arm/kits.json), the manifest of their files (data/arm/manifest.json) and the reports of the core
(data/arm/reports). Into git go counts under neutral ids: no file names, designations or values.

- ``register`` adds a kit folder of data/arm under an id, with its role in the split: ``dev`` — rules are tuned on
  it, ``test`` — measured on frozen rules, ``aside`` — outside the split;
- ``verify`` compares the files with the manifest;
- ``run`` checks the manifest, runs the baseline pipeline with OCR on every kit, keeps the reports in
  data/arm/reports and writes eval/results/arm-kits.json and eval/results/arm-kits.md. With ``--role dev`` it runs
  the kits of that role only and writes eval/results/arm-kits-dev.*: the rules are tuned on dev between the runs
  of all kits, so the test kits are not looked at for every change.

There is no truth yet, so a cell counts as filled, not as right. The one check against something outside the core is
the kind of a document by the code the customer writes into the file name: «СП» or «СБ». ``--vlm MODEL`` runs the
core with a model on the server of this machine on title blocks and specification rows (ADR-0019, ADR-0021) and
writes eval/results/arm-kits[-<role>]-vlm.*. Images get the statistics of eval/scan_quality.py, the same as the real
scans of the gold set, to tell a scan from a raster exported from CAD.

    uv run python eval/arm_kits.py register arm-1 data/arm/raw/<папка> --role dev
    uv run python eval/arm_kits.py verify
    uv run python eval/arm_kits.py run [--role dev] [--vlm qwen3.6-35b-a3b-mlx]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kdcheck.normalize.designations import Designations
from kdcheck.pipeline import BaselinePipeline
from kdcheck.report.labels import PARAMETER_CATEGORY
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Extracted, Report
from scan_quality import image_statistics, page_image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "arm"
KITS = DATA / "kits.json"
MANIFEST = DATA / "manifest.json"
REPORTS = DATA / "reports"
OUT = Path(__file__).resolve().parent / "results"

ROLES = ("dev", "test", "aside")
# The document code the customer writes into a file name after the designation: «… СБ …», «…-5000047СБ - …».
NAME_CODE = re.compile(r"(?<=[\s\d])(СБ|СП)(?=[\s(]|$)")
NAME_KINDS = {"СП": "СП", "СБ": "СБ", None: "прочие"}
SHEET_SUFFIX = re.compile(r"\s*\(\d+\)$")  # «…(2)»: a sheet of a document the customer keeps one sheet per file
CORE_KINDS = {
    "specification": "спецификация",
    "assembly_drawing": "сборочный чертёж",
    "part_drawing": "чертёж детали",
    "drawing": "чертёж",
    "other": "прочий",
    "unknown": "не определён",
}
TITLE_FIELDS = {
    "designation": "обозначение",
    "name": "наименование",
    "material": "материал",
    "mass": "масса",
    "scale": "масштаб",
    "litera": "литера",
    "revision": "изм.",
    "sheets": "листов",
}
ORIGINS = {"vector": "вектор", "scan": "изображение", "mixed": "смешанный", "unknown": "не определён"}
IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
IMAGE_STATS = {"dpi": "dpi", "paper": "Бумага", "specks": "Крап", "uneven": "Неровность фона", "skew": "Наклон, °"}


# ------------------------------------------------------------------------------------------- register and manifest


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


KIT_NOTE = re.compile(r"^Спецификация, строка .+: прочитано «.*», в комплекте «.+»$")


def files_of(folder: Path) -> dict[str, str]:
    """Every file the core would see in a kit folder, by its path from the folder, with its sha256."""
    return {p.relative_to(folder).as_posix(): sha256(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def register(kit: str, folder: Path, role: str) -> int:
    folder = folder.resolve()
    if not folder.is_dir() or not folder.is_relative_to(DATA):
        print(f"Нужна папка комплекта внутри {DATA}: {folder}")
        return 2
    kits, manifest = load(KITS), load(MANIFEST)
    kits[kit] = {"path": folder.relative_to(DATA).as_posix(), "role": role}
    manifest[kit] = files_of(folder)
    save(KITS, dict(sorted(kits.items())))
    save(MANIFEST, dict(sorted(manifest.items())))
    print(f"{kit}: {len(manifest[kit])} файлов, роль {role}")
    return 0


def verify(kits: dict[str, Any]) -> bool:
    """Whether every kit holds exactly the files of the manifest; prints the counts, never the names."""
    manifest, same = load(MANIFEST), True
    for kit, entry in kits.items():
        want, have = manifest.get(kit, {}), files_of(DATA / entry["path"])
        counts = Counter(
            "совпал" if have.get(path) == digest else ("изменён" if path in have else "пропал")
            for path, digest in want.items()
        )
        counts["лишний"] = len(set(have) - set(want))
        print(f"{kit}: " + ", ".join(f"{label} {n}" for label, n in counts.items() if n))
        same = same and counts["совпал"] == len(want) == len(have)
    return same


# ------------------------------------------------------------------------------------------------------- the run


def _filled(value: Extracted | None) -> bool:
    return value is not None and value.value not in (None, "")


def _name_kind(file_name: str) -> str:
    m = NAME_CODE.search(Path(file_name).stem)
    return NAME_KINDS[m.group(1) if m else None]


def _spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def images(folder: Path) -> dict[str, Any]:
    """Statistics of every image of a kit by the rules of eval/scan_quality.py: median, minimum and maximum."""
    rows = [image_statistics(*page_image(p)[:2]) for p in sorted(folder.rglob("*")) if p.suffix.lower() in IMAGES]
    return {"count": len(rows), **{key: _spread([r[key] for r in rows]) for key in IMAGE_STATS}}


def aggregate(report: Report, seconds: float, eskd: Designations) -> dict[str, Any]:
    """What the core built on a kit, in counts."""
    docs = report.documents
    pages = sum(len(d.pages) for d in docs)
    by_name = Counter(SHEET_SUFFIX.sub("", Path(d.file_name).stem) for d in docs)
    multi = [n for n in by_name.values() if n > 1]
    kinds: dict[str, Counter[str]] = {label: Counter() for label in dict.fromkeys(NAME_KINDS.values())}
    for d in docs:
        kinds[_name_kind(d.file_name)][d.kind] += 1
    rows = [row for spec in report.specifications for row in spec.rows]
    return {
        "files": len(docs),
        "pages": pages,
        "documents_by_name": len(by_name),
        "multi_sheet": len(multi),
        "multi_sheet_files": sum(multi),
        "origin": dict(Counter(d.origin for d in docs)),
        "status": dict(Counter(d.status for d in docs)),
        "kinds": {label: dict(c) for label, c in kinds.items() if c},
        "documents_with_designation": sum(1 for d in docs if d.designation),
        "specifications": len(report.specifications),
        "specifications_with_designation": sum(1 for s in report.specifications if _filled(s.designation)),
        "spec_rows": len(rows),
        "rows_with_designation": sum(1 for r in rows if _filled(r.designation)),
        "rows_designation_parsed": sum(
            1 for r in rows if _filled(r.designation) and eskd.normalize_designation(str(r.designation.value))
        ),
        # rows whose reading the core checked against the designations of the kit and replaced (ADR-0020), by the
        # notes of the stage: the conflict status is set by the model as well (ADR-0019)
        "rows_by_kit": sum(1 for d in docs for note in d.notes if KIT_NOTE.search(note)),
        "drawings": len(report.parts),
        "title_block": {f: sum(1 for p in report.parts if _filled(getattr(p.title_block, f))) for f in TITLE_FIELDS},
        # Block 2: technical requirements of part drawings and the parameters of every drawing, by category
        "part_drawings": sum(1 for p in report.parts if p.kind == "part_drawing"),
        "part_drawings_with_requirements": sum(1 for p in report.parts if p.kind == "part_drawing" and p.requirements),
        "requirement_items": sum(len(p.requirements) for p in report.parts),
        "requirements_by_model": sum(
            1 for p in report.parts for i in p.requirements if i.source is not None and i.source.method == "vlm"
        ),
        "parameters": dict(Counter(x.category for p in report.parts for x in p.parameters)),
        "tree_items": report.summary.tree_items,
        "root": report.kit.root_designation is not None,
        "discrepancies": dict(Counter(x.rule_id or x.type for x in report.discrepancies)),
        "log": dict(Counter(e.level for e in report.log)),
        "seconds": round(seconds, 1),
        "seconds_per_page": round(seconds / max(pages, 1), 2),
    }


def run(kits: dict[str, Any], use_ocr: bool, model: str | None = None) -> tuple[dict[str, Any], dict[str, str | None]]:
    settings = load_settings(use_ocr=use_ocr, use_vlm=model is not None)
    if model is not None:  # a model on the server of this machine: the files never leave it (allow_remote: false)
        vlm = settings.models.vlm.model_copy(update={"model": model})
        settings = settings.model_copy(update={"models": settings.models.model_copy(update={"vlm": vlm})})
    pipeline = BaselinePipeline(settings)
    REPORTS.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {}
    engines: dict[str, str | None] = {}
    for kit, entry in kits.items():
        folder = DATA / entry["path"]
        start = time.perf_counter()
        report = pipeline.run(folder, kit_id=kit, kit_name=kit)
        seconds = time.perf_counter() - start
        name = f"{kit}-vlm" if model else kit
        (REPORTS / f"{name}.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        for e in report.kit.engines:  # a model reads with two prompts: both versions are kept
            versions = {*(engines.get(e.name) or "").split("; "), e.version or ""} - {""}
            engines[e.name] = "; ".join(sorted(versions)) or None
        result[kit] = {"role": entry["role"], **aggregate(report, seconds, pipeline.eskd), "images": images(folder)}
        print(f"{kit}: {result[kit]['pages']} листов, {seconds:.0f} с")
    return result, engines


# -------------------------------------------------------------------------------------------------- the results


def _counts(counts: dict[str, int], labels: dict[str, str]) -> str:
    return ", ".join(f"{labels.get(key, key)} {n}" for key, n in counts.items()) or "—"


def _value(spread: dict[str, float] | None) -> str:
    if spread is None:
        return "—"
    if spread["min"] == spread["max"]:
        return f"{spread['median']:g}"
    return f"{spread['median']:g} ({spread['min']:g}…{spread['max']:g})"


def markdown(result: dict[str, Any]) -> str:
    kits = result["kits"]
    engines = ", ".join(f"{name} {version or ''}".strip() for name, version in sorted(result["engines"].items()))
    lines = [
        "# Комплекты АРМ: прогон ядра",
        "",
        f"Получено `uv run python eval/arm_kits.py run{' --role ' + result['role'] if result['role'] else ''}"
        f"{' --vlm ' + result['model'] if result.get('model') else ''}` "
        f"{result['date']}; {result['machine']}; {engines}; "
        f"OCR {'включён' if result['ocr'] else 'выключен'}. Комплекты — данные заказчика: они лежат в `data/arm` "
        "вне git, папки и sha256 файлов — в реестре и манифесте там же. Здесь только числа под обезличенными "
        "идентификаторами, без имён файлов, обозначений и значений; полностью — в одноимённом JSON.",
        "",
        "Роль — место в разбиении: на `dev` настраиваются правила, `test` меряется один раз на замороженных "
        "правилах, `aside` в разбиение не входит. Эталона пока нет, поэтому графа считается заполненной, а не "
        "прочитанной верно.",
        "",
        "## Комплекты",
        "",
        "| Комплект | Роль | Файлов | Листов | Вход | По кодам в именах файлов | Документов по именам "
        "| Время, с | с на лист |",
        "| --- | --- | ---: | ---: | --- | --- | --- | ---: | ---: |",
    ]
    for kit, k in kits.items():
        by_name = {label: sum(c.values()) for label, c in k["kinds"].items()}
        documents = str(k["documents_by_name"])
        if k["multi_sheet"]:
            documents += f", из них {k['multi_sheet']} — по листу в файле, на {k['multi_sheet_files']} файлах"
        lines.append(
            f"| `{kit}` | {k['role']} | {k['files']} | {k['pages']} | {_counts(k['origin'], ORIGINS)} "
            f"| {_counts(by_name, {})} | {documents} | {k['seconds']:g} | {k['seconds_per_page']:g} |"
        )
    lines += [
        "",
        "## Изображения: скан или растр",
        "",
        "Статистики [scan-quality.md](scan-quality.md), те же, что у реальных сканов эталона: медиана, в скобках — "
        "от и до по всем изображениям комплекта. Бумага — уровень серого от 0 до 255, крап — мм² на дм². У растра, "
        "выгруженного из CAD, бумага — 255, крапа, неровности фона и наклона нет.",
        "",
        "| Комплект | Изображений | " + " | ".join(IMAGE_STATS.values()) + " |",
        "| --- | ---: | " + " | ".join("---" for _ in IMAGE_STATS) + " |",
    ]
    for kit, k in kits.items():
        if k["images"]["count"]:
            cells = " | ".join(_value(k["images"][key]) for key in IMAGE_STATS)
            lines.append(f"| `{kit}` | {k['images']['count']} | {cells} |")
    lines += [
        "",
        "## Что построило ядро",
        "",
        "Обозначение строки «заполнено» — в графе есть текст, «по грамматике» — в нём нашлось обозначение "
        "одной из грамматик ядра, «по комплекту» — ядро заменило прочтение обозначением документа комплекта с теми же "
        "цифрами ([ADR-0020](../../docs/adr/0020-row-designations-by-kit.md)).",
        "",
        "| Комплект | Спецификаций | из них с обозначением | Строк | обозначение заполнено | по грамматике "
        "| по комплекту | Документов с обозначением | Элементов дерева | Корень | Расхождений | Проблемных файлов |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for kit, k in kits.items():
        problems = sum(n for status, n in k["status"].items() if status not in ("ok", "warning"))
        lines.append(
            f"| `{kit}` | {k['specifications']} | {k['specifications_with_designation']} | {k['spec_rows']} "
            f"| {k['rows_with_designation']} | {k['rows_designation_parsed']} | {k.get('rows_by_kit', '—')} "
            f"| {k['documents_with_designation']} из {k['files']} "
            f"| {k['tree_items']} | {'найден' if k['root'] else 'нет'} | {sum(k['discrepancies'].values())} "
            f"| {problems} |"
        )
    lines += [
        "",
        "## Вид документа: код в имени файла против ядра",
        "",
        "| Комплект | Код в имени | " + " | ".join(CORE_KINDS.values()) + " |",
        "| --- | --- | " + " | ".join("---:" for _ in CORE_KINDS) + " |",
    ]
    for kit, k in kits.items():
        for label, c in k["kinds"].items():
            cells = " | ".join(str(c.get(kind, 0)) for kind in CORE_KINDS)
            lines.append(f"| `{kit}` | {label} | {cells} |")
    lines += [
        "",
        "## Основная надпись чертежей: заполненные графы",
        "",
        "| Комплект | Чертежей | " + " | ".join(TITLE_FIELDS.values()) + " |",
        "| --- | ---: | " + " | ".join("---:" for _ in TITLE_FIELDS) + " |",
    ]
    for kit, k in kits.items():
        cells = " | ".join(str(k["title_block"][f]) for f in TITLE_FIELDS)
        lines.append(f"| `{kit}` | {k['drawings']} | {cells} |")
    lines += [
        "",
        "## Блок 2: технические требования и параметры",
        "",
        "Пункты технических требований — у чертежей деталей; «моделью» — пункты, которые прочла модель "
        "([ADR-0023](../../docs/adr/0023-vlm-tech-requirements.md)). Параметры — у всех чертежей, по категориям.",
        "",
        "| Комплект | Чертежей деталей | с пунктами ТТ | Пунктов | моделью | "
        + " | ".join(PARAMETER_CATEGORY.values())
        + " |",
        "| --- | ---: | ---: | ---: | ---: | " + " | ".join("---:" for _ in PARAMETER_CATEGORY) + " |",
    ]
    for kit, k in kits.items():
        if "parameters" not in k:
            continue
        cells = " | ".join(str(k["parameters"].get(c, 0)) for c in PARAMETER_CATEGORY)
        lines.append(
            f"| `{kit}` | {k['part_drawings']} | {k['part_drawings_with_requirements']} | {k['requirement_items']} "
            f"| {k['requirements_by_model']} | {cells} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("register", help="добавить папку комплекта под идентификатором")
    p.add_argument("kit")
    p.add_argument("folder", type=Path)
    p.add_argument("--role", choices=ROLES, required=True)
    sub.add_parser("verify", help="сверить файлы комплектов с манифестом")
    p = sub.add_parser("run", help="прогнать ядро на комплектах")
    p.add_argument("--role", choices=ROLES, help="только комплекты этой роли; результаты — в arm-kits-<роль>")
    p.add_argument(
        "--vlm", metavar="MODEL", help="с этой моделью на основных надписях и строках; результаты — в arm-kits-…-vlm"
    )
    args = parser.parse_args(argv)
    if args.command == "register":
        return register(args.kit, args.folder, args.role)
    kits = load(KITS)
    if args.command == "verify":
        return 0 if verify(kits) else 1
    chosen = {kit: entry for kit, entry in kits.items() if args.role in (None, entry["role"])}
    if not chosen:
        print(f"В реестре нет комплектов роли {args.role}")
        return 2
    if not verify(chosen):
        print("Файлы комплектов не совпадают с манифестом: прогон остановлен")
        return 1
    use_ocr = ocr_available()
    model = getattr(args, "vlm", None)
    measured, engines = run(chosen, use_ocr, model)
    result = {
        "model": model,
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "role": args.role,
        "ocr": use_ocr,
        "engines": engines,
        "kits": measured,
    }
    stem = (f"arm-kits-{args.role}" if args.role else "arm-kits") + ("-vlm" if model else "")
    OUT.mkdir(exist_ok=True)
    (OUT / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / f"{stem}.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
