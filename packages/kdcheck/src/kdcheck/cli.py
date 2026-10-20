"""Command line: ``kdcheck run FOLDER -o REPORT_FOLDER``."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from kdcheck.pipeline import BaselinePipeline
from kdcheck.report import write_report


def _progress(stage: str, value: float, message: str | None = None) -> None:
    print(f"\r[{value:4.0%}] {stage}: {(message or '')[:70]:<70}", end="", file=sys.stderr, flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input).resolve()
    if not input_dir.is_dir():
        print(f"Папка не найдена: {input_dir}", file=sys.stderr)
        return 2
    out = Path(args.output or f"{input_dir.name}_report")
    pipeline = BaselinePipeline(use_ocr=not args.no_ocr)
    report = pipeline.run(
        input_dir, kit_id=uuid.uuid4().hex[:12], kit_name=args.name or input_dir.name, progress=_progress
    )
    print(file=sys.stderr)
    write_report(report, out)
    s = report.summary
    print(
        f"Документов: {s.documents_total}, расхождений: {s.discrepancies_total}, "
        f"критичных параметров: {s.critical_parameters}"
    )
    print(f"Отчёт: {out / 'report.json'} (+ report.xlsx, report.html)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kdcheck", description="Входной контроль комплекта КД")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="обработать папку с комплектом")
    p.add_argument("input", help="папка с комплектом")
    p.add_argument("-o", "--output", help="папка отчёта (по умолчанию <папка>_report)")
    p.add_argument("--name", help="название комплекта в отчёте")
    p.add_argument("--no-ocr", action="store_true", help="не распознавать сканы")
    p.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result
