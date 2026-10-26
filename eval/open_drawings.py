"""The open drawings corpus (plan stage 2.1): the registry eval/open_drawings.csv in git, the files outside git.

The registry lists every document with its link, year, period of the drafting standards and sha256. ``fetch``
downloads what is missing into data/open_drawings and checks the hash, so anyone can rebuild the same corpus;
the files themselves never enter the repository: their licenses allow internal evaluation only. ``summary`` writes
the counts by period and category to eval/results/open-drawings.md.

    uv run python eval/open_drawings.py fetch
    uv run python eval/open_drawings.py summary
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).resolve().parent / "open_drawings.csv"
CORPUS = ROOT / "data" / "open_drawings"
OUT = Path(__file__).resolve().parent / "results" / "open-drawings.md"

# Periods of the drafting standards: the title block and the specification changed form between them.
PERIODS = {
    "pre_eskd": "до ЕСКД, до 1971",
    "ussr": "СССР, год не указан",
    "eskd_ussr": "ЕСКД в СССР, 1971–1991",
    "eskd_1992_2005": "1992–2005",
    "eskd_2006": "ГОСТ 2.104-2006, 2006–2024",
    "eskd_2023": "ГОСТ Р 2.104-2023, с 2024",
}
CATEGORIES = {
    "production": "производственная КД",
    "catalog": "каталоги",
    "educational": "учебные",
    "normative": "стандарты",
}


def read_registry() -> list[dict[str, str]]:
    with REGISTRY.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch() -> int:
    failed = 0
    for row in read_registry():
        target = CORPUS / row["path"]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            try:
                request = urllib.request.Request(row["source_url"], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as f:
                    while chunk := response.read(1 << 20):
                        f.write(chunk)
            except OSError as e:  # one dead link must not stop the rest
                print(f"не скачан: {row['path']}: {e}")
                part.unlink(missing_ok=True)
                failed += 1
                continue
            part.rename(target)
        if sha256(target) != row["sha256"]:
            print(f"хэш не совпал: {row['path']}")
            failed += 1
    print(f"документов в реестре: {len(read_registry())}, с ошибкой: {failed}")
    return 1 if failed else 0


def summary() -> int:
    rows = read_registry()
    docs: Counter[tuple[str, str]] = Counter()
    pages: Counter[tuple[str, str]] = Counter()
    sheets: Counter[tuple[str, str]] = Counter()
    for row in rows:
        key = (row["period"], row["category"])
        docs[key] += 1
        pages[key] += int(row["pages"])
        sheets[key] += int(row["sheets"] or 0)
    lines = [
        "# Открытые чертежи: состав корпуса",
        "",
        "Сводка по реестру [open_drawings.csv](../open_drawings.csv), скрипт `eval/open_drawings.py summary`.",
        "«Листы» — страницы с чертежом, схемой или спецификацией и основной надписью; их считали глазами по",
        "миниатюрам всех страниц, у документов без подсчёта графа пустая и в сумму не входит.",
        "",
        "| Период | Категория | Документов | Страниц | Листов |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for period, period_name in PERIODS.items():
        for category, category_name in CATEGORIES.items():
            key = (period, category)
            if docs[key]:
                lines.append(f"| {period_name} | {category_name} | {docs[key]} | {pages[key]} | {sheets[key] or '—'} |")
    lines.append(f"| Всего | | {sum(docs.values())} | {sum(pages.values())} | {sum(sheets.values())} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("command", choices=["fetch", "summary"])
    args = parser.parse_args()
    return fetch() if args.command == "fetch" else summary()


if __name__ == "__main__":
    raise SystemExit(main())
