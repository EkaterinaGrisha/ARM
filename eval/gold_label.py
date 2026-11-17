"""A local page to label a customer's kit by its masked sheets (plan stage 8), in the format eval/gold.py checks.

The page shows the masked copies of data/arm/masked/<kit>, sheet by sheet, next to a form of the document: its kind,
the title block, the rows of a specification, the items of the technical requirements and the parameters of a drawing,
each item and parameter with its region drawn on the sheet by the mouse. The labelling is
data/arm/gold/<kit>.<label>.json; every save checks it as ``eval/gold.py check`` does and keeps the previous version in
<kit>.<label>.bak. A new labelling starts from the file names alone, and the page reads no other labelling and no
report of the core, so the labelling stays independent. The server listens on 127.0.0.1 only, and the sheets stay on
the machine.

    uv run python eval/gold_label.py arm-1 --by a
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, get_args

import gold
from kdcheck.schemas import ParameterCategory
from PIL import Image
from pydantic import ValidationError
from synth.truth import KitTruth

PAGE = Path(__file__).with_name("gold_label.html")
LATER_SHEET = re.compile(r"(?P<stem>.*?)\s*\((?P<n>\d+)\)(?P<ext>\.\w+)$")  # «… СП Мост(2).jpg»: sheet 2


def template(names: list[str]) -> KitTruth:
    """An empty labelling of a kit by its file names: files «…(N)» of one name are the sheets of one document, the code
    «СП» or «СБ» in a name gives a specification or an assembly drawing, and any other name a part drawing."""
    groups: dict[str, dict[str, int]] = {}
    for name in names:
        m = LATER_SHEET.match(name)
        key, sheet = (m["stem"] + m["ext"], int(m["n"])) if m else (name, 1)
        if sheet in groups.get(key, {}).values():
            key = name
        groups.setdefault(key, {})[name] = sheet
    truth = KitTruth(root="", documents={})
    for files in groups.values():
        first = min(files, key=files.__getitem__)
        code = re.search(r"\b(СП|СБ)\b", first)
        kind = {"СП": "specification", "СБ": "assembly_drawing"}.get(code[1] if code else "", "part_drawing")
        doc = gold.blank(kind, "vector" if first.lower().endswith(".pdf") else "raster", len(files))
        if len(files) > 1:
            doc.sheet_files = files
        truth.documents[first] = doc
    return truth


def problems(truth: KitTruth, names: set[str]) -> list[str]:
    unknown = [f"{name} — нет в манифесте комплекта" for name in gold._files(truth) if name not in names]
    return unknown + gold.content_problems(truth)


class Kit:
    """A kit to label: its files, its masked sheets with their sizes, and the labelling file of one labeller."""

    def __init__(self, arm: Path, kit: str, label: str) -> None:
        self.kit, self.label = kit, label
        self.names = set(json.loads((arm / "manifest.json").read_text(encoding="utf-8"))[kit])
        masked = arm / "masked" / kit
        listed = json.loads((masked / "manifest.json").read_text(encoding="utf-8"))["sheets"]
        self.sheets: list[dict[str, Any]] = []
        self.unmasked = sorted({s["file"] for s in listed if not s.get("written")})
        for s in listed:
            if s.get("written"):
                with Image.open(masked / s["written"]) as image:
                    width, height = image.size
                self.sheets.append(
                    {
                        "file": s["file"],
                        "page": s["page"],
                        "path": masked / s["written"],
                        "width": width,
                        "height": height,
                    }
                )
        self.path = arm / "gold" / f"{kit}.{label}.json"

    def state(self) -> dict[str, Any]:
        truth = KitTruth.read(self.path) if self.path.exists() else template(sorted(self.names))
        return {
            "kit": self.kit,
            "label": self.label,
            "saved": self.path.exists(),
            "sheets": [{k: v for k, v in s.items() if k != "path"} for s in self.sheets],
            "unmasked": self.unmasked,
            "truth": truth.model_dump(mode="json"),
            "categories": list(get_args(ParameterCategory)),
            "types": list(gold.TYPES),
            "problems": problems(truth, self.names),
        }

    def save(self, data: Any) -> list[str]:
        truth = KitTruth.model_validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = self.path.with_suffix(".tmp")
        truth.write(fresh)
        if self.path.exists():
            self.path.replace(self.path.with_suffix(".bak"))
        fresh.replace(self.path)
        return problems(truth, self.names)


def handler(kit: Kit) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: HTTPStatus, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: HTTPStatus, data: Any) -> None:
            self.send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if self.path == "/":
                self.send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/kit":
                self.send_json(HTTPStatus.OK, kit.state())
            elif m := re.fullmatch(r"/sheet/(\d+)", self.path):
                index = int(m[1])
                if index >= len(kit.sheets):
                    self.send(HTTPStatus.NOT_FOUND, b"", "text/plain")
                    return
                self.send(HTTPStatus.OK, kit.sheets[index]["path"].read_bytes(), "image/png")
            else:
                self.send(HTTPStatus.NOT_FOUND, b"", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/save":
                self.send(HTTPStatus.NOT_FOUND, b"", "text/plain")
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            try:
                self.send_json(HTTPStatus.OK, {"problems": kit.save(json.loads(body))})
            except ValidationError as exc:
                self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("kit", help="комплект АРМ, например arm-1")
    parser.add_argument("--by", required=True, help="метка разметки в имени файла: a, b")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--arm", type=Path, default=gold.ARM, help="папка данных заказчика, по умолчанию data/arm")
    args = parser.parse_args(argv)
    if not args.by or "." in args.by:
        print("метка — короткий тег без точки: a, b", file=sys.stderr)
        return 1
    kit = Kit(args.arm, args.kit, args.by)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(kit))
    print(f"разметка {kit.path.name}: http://127.0.0.1:{args.port}/ (остановить — Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
