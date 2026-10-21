"""Command line: ``kdcheck run FOLDER -o REPORT_FOLDER``."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from kdcheck.report import write_report
from kdcheck.run import ProgressCallback, run


def _progress(stage: str, value: float, message: str | None = None) -> None:
    print(f"\r[{value:4.0%}] {stage}: {(message or '')[:70]:<70}", end="", file=sys.stderr, flush=True)


def _progress_to_file(path: Path) -> ProgressCallback:
    """Also write each step to ``path`` as ``{"stage", "progress", "message"}`` for the API to poll."""

    def progress(stage: str, value: float, message: str | None = None) -> None:
        _progress(stage, value, message)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"stage": stage, "progress": value, "message": message}, ensure_ascii=False))
        tmp.replace(path)

    return progress


def cmd_run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input).resolve()
    if not input_dir.is_dir():
        print(f"Папка не найдена: {input_dir}", file=sys.stderr)
        return 2
    out = Path(args.output or f"{input_dir.name}_report")
    report = run(
        input_dir,
        kit_id=args.kit_id or uuid.uuid4().hex[:12],
        kit_name=args.name or input_dir.name,
        use_ocr=not args.no_ocr,
        progress=_progress_to_file(Path(args.progress_file)) if args.progress_file else _progress,
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
    p.add_argument("--kit-id", help="идентификатор комплекта в отчёте (по умолчанию случайный)")
    p.add_argument("--progress-file", help="писать ход обработки в JSON-файл")
    p.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result
