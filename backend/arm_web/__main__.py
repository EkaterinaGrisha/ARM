"""Командная строка.

python -m arm_web serve [--host 0.0.0.0] [--port 8000]       веб-интерфейс и API
python -m arm_web process ПАПКА [-o ОТЧЁТ] [--truth ЭТАЛОН]   пакетная обработка комплекта
python -m arm_web demo-kit ПАПКА                             сгенерировать демо-комплект
python -m arm_web evaluate report.json truth.json [--write]  метрики качества по эталону
python -m arm_web build-demo                                 демо-данные для фронтенда
python -m arm_web schema                                     JSON Schema отчёта
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("arm_web.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_process(args) -> int:
    from kdcheck.report import write_report

    from .evaluate import evaluate
    from .pipeline import get_pipeline

    input_dir = Path(args.input).resolve()
    if not input_dir.is_dir():
        print(f"Папка не найдена: {input_dir}", file=sys.stderr)
        return 2
    out = Path(args.output or f"{input_dir.name}_report")
    pipeline = get_pipeline()

    def progress(stage: str, value: float, message: str | None = None) -> None:
        print(f"\r[{value:4.0%}] {stage}: {(message or '')[:70]:<70}", end="", file=sys.stderr, flush=True)

    report = pipeline.run(
        input_dir, kit_id=uuid.uuid4().hex[:12], kit_name=args.name or input_dir.name, progress=progress
    )
    print(file=sys.stderr)
    if args.truth:
        report.quality = evaluate(
            report, json.loads(Path(args.truth).read_text(encoding="utf-8")), dataset=Path(args.truth).name
        )
    write_report(report, out)
    s = report.summary
    print(
        f"Документов: {s.documents_total}, расхождений: {s.discrepancies_total}, "
        f"критичных параметров: {s.critical_parameters}"
    )
    print(f"Отчёт: {out / 'report.json'} (+ report.xlsx, report.html)")
    return 0


def cmd_demo_kit(args) -> int:
    from .demo_kit import main

    return main([args.output])


def cmd_evaluate(args) -> int:
    from .evaluate import main

    return main([args.report, args.truth] + (["--write"] if args.write else []))


def cmd_build_demo(args) -> int:
    from .build_demo import build

    build(REPO / "samples", REPO / "frontend" / "public" / "demo", regenerate=args.regenerate)
    return 0


def cmd_schema(args) -> int:
    from .models import Report

    print(json.dumps(Report.model_json_schema(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arm_web", description="Входной контроль комплекта КД")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="запустить веб-интерфейс")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("process", help="обработать папку с комплектом")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--name")
    p.add_argument("--truth", help="эталон для расчёта метрик качества")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("demo-kit", help="сгенерировать демо-комплект")
    p.add_argument("output", nargs="?", default="samples/demo_kit")
    p.set_defaults(func=cmd_demo_kit)

    p = sub.add_parser("evaluate", help="метрики качества по эталону")
    p.add_argument("report")
    p.add_argument("truth")
    p.add_argument("--write", action="store_true", help="записать метрики в report.json")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("build-demo", help="собрать демо-данные для фронтенда")
    p.add_argument("--regenerate", action="store_true", help="пересоздать демо-комплект")
    p.set_defaults(func=cmd_build_demo)

    p = sub.add_parser("schema", help="JSON Schema отчёта")
    p.set_defaults(func=cmd_schema)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
