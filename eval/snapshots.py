"""Reports of the core against saved snapshots: any change of behaviour stops the check (plan, item «а» of 19.11).

``record`` runs the core on every kit of the targets and keeps its report without the time of processing; ``check``
runs it again, prints what changed value by value and exits with 1. Targets: ``demo`` — the demo kit, snapshots in
samples/snapshots; ``synth`` — the corpus of synth_kits.py in data/synth/kits; ``arm`` — the dev kits of the customer,
checked against their manifest first. Snapshots of the last two are kept in data/snapshots, outside git: the reports of
``arm`` hold customer data. Readings go through the read cache (ADR-0018), data/cache unless ARM_CACHE_DIR names
another: Tesseract and the model are not called again, and on the machine of 19.11 a check of all three targets takes
2.5 min, the demo kit 2 s. ``--text`` reads without OCR: the snapshot of
the demo kit read so is checked by the tests in CI, where the versions of Tesseract differ from machine to machine.

    uv run python eval/snapshots.py record demo synth arm
    uv run python eval/snapshots.py check demo synth arm --vlm qwen3.6-35b-a3b-mlx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import arm_kits
from kdcheck import cache as read_cache
from kdcheck.run import BaselinePipeline, load_settings, ocr_available
from kdcheck.schemas import Report

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
CORPUS = ROOT / "data" / "synth" / "kits"
TARGETS = ("demo", "synth", "arm")
SHOWN = 40  # changes printed per kit; the count is always printed
ABSENT = object()


def kits(target: str) -> dict[str, Path]:
    if target == "demo":
        return {"demo": ROOT / "samples" / "demo_kit"}
    if target == "synth":
        if not CORPUS.is_dir():
            raise SystemExit(f"Нет корпуса {CORPUS}: его собирает eval/synth_kits.py")
        return {p.name: p for p in sorted(CORPUS.iterdir()) if p.is_dir()}
    dev = {kit: entry for kit, entry in arm_kits.load(arm_kits.KITS).items() if entry["role"] == "dev"}
    if not arm_kits.verify(dev):
        raise SystemExit("Файлы комплектов заказчика не совпадают с манифестом")
    return {kit: arm_kits.DATA / entry["path"] for kit, entry in dev.items()}


def folder(target: str) -> Path:
    return ROOT / "samples" / "snapshots" if target == "demo" else ROOT / "data" / "snapshots" / target


def _rounded(value: Any) -> Any:
    # the last bits of a float may differ between platforms of CI with the same libraries
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def snapshot(report: Report) -> dict[str, Any]:
    data: dict[str, Any] = _rounded(report.model_dump(mode="json"))
    for key in ("created_at", "processed_at", "duration_sec"):
        data["kit"][key] = None
    return data


def _key(item: Any, index: int) -> str:
    """Documents by path and findings by rule and message, so that one added item does not shift all after it."""
    if not isinstance(item, dict):
        return str(index)
    if "path" in item:
        return str(item["path"])
    if "rule_id" in item and "message" in item:
        return f"{item['rule_id']}: {item['message']}"
    return str(item.get("id", item.get("document_id", index)))


def _keyed(items: list[Any]) -> dict[str, Any]:
    keyed: dict[str, Any] = {}
    for index, item in enumerate(items):
        key = _key(item, index)
        while key in keyed:
            key += "+"
        keyed[key] = item
    return keyed


def _short(value: Any) -> str:
    if value is ABSENT:
        return "нет"
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 160 else text[:157] + "..."


def changes(old: Any, new: Any, where: str = "") -> list[str]:
    """What differs between two reports as JSON, one line per value: «where: old → new»."""
    if isinstance(old, dict) and isinstance(new, dict):
        return [
            line
            for key in {**old, **new}
            for line in changes(old.get(key, ABSENT), new.get(key, ABSENT), f"{where}.{key}" if where else key)
        ]
    if isinstance(old, list) and isinstance(new, list):
        a, b = _keyed(old), _keyed(new)
        return [
            line for key in {**a, **b} for line in changes(a.get(key, ABSENT), b.get(key, ABSENT), f"{where}[{key}]")
        ]
    return [] if old == new else [f"{where}: {_short(old)} → {_short(new)}"]


def run(targets: list[str], record: bool, model: str | None, text: bool) -> int:
    settings = load_settings(use_ocr=not text, use_vlm=model is not None)
    if not text and not ocr_available(settings):
        print("Нет Tesseract: без него снимки снимаются только с --text")
        return 2
    if model is not None:
        vlm = settings.models.vlm.model_copy(update={"model": model})
        settings = settings.model_copy(update={"models": settings.models.model_copy(update={"vlm": vlm})})
    pipeline = BaselinePipeline(settings)
    suffix = "-text" if text else "-vlm" if model else ""
    changed = 0
    with read_cache.use(read_cache.folder_from_env() or CACHE):
        for target in targets:
            out = folder(target)
            out.mkdir(parents=True, exist_ok=True)
            for kit, path in kits(target).items():
                report = snapshot(pipeline.run(path, kit_id=kit, kit_name=kit))
                file = out / f"{kit}{suffix}.json"
                if record:
                    file.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                    print(f"{target}/{kit}: снимок записан")
                    continue
                if not file.exists():
                    print(f"{target}/{kit}: снимка нет, сначала record")
                    changed += 1
                    continue
                lines = changes(json.loads(file.read_text(encoding="utf-8")), report)
                print(f"{target}/{kit}: " + (f"изменений {len(lines)}" if lines else "без изменений"))
                for line in lines[:SHOWN]:
                    print(f"    {line}")
                changed += bool(lines)
    return 1 if changed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("command", choices=("record", "check"))
    parser.add_argument("targets", nargs="+", choices=TARGETS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--vlm", metavar="MODEL", help="с этой моделью; снимки — с суффиксом -vlm")
    mode.add_argument("--text", action="store_true", help="без OCR, только текстовый слой; снимки — с суффиксом -text")
    args = parser.parse_args(argv)
    return run(args.targets, args.command == "record", args.vlm, args.text)


if __name__ == "__main__":
    sys.exit(main())
