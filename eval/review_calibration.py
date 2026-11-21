"""The review mark calibrated (plan, step 6): the confidence below which the report marks a value «проверить».

A value the report gives is marked when it is taken from the file name, when two readers disagree on it (status
``conflict``) or when its confidence is below the threshold, ``review.min_confidence`` of configs/ingest.yaml. Values
of rasters and scans count: a vector value comes from the text layer. Their records come from the customer's dev kit
(eval/arm_gold.py, data/arm/eval) and the frozen synthetic corpus (eval/reading_errors.py, data/reading), its stress
part left out. Documents fall into two halves by the sha256 of their corpus, kit and name, so a document is never in
both: on the first the threshold is the lowest of the grid at which the unmarked values hold at most the target share
of wrong ones, the second checks it. Each reading configuration, OCR and OCR with the model, gets its own threshold.
The interval of the share of wrong unmarked values is a bootstrap over the documents of the second half: the values of
one document are not independent.

Writes eval/results/review-calibration.json and .md.

    uv run python eval/review_calibration.py [--target 0.02]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "results"
SOURCES = {
    "ocr": [ROOT / "data/arm/eval/arm-gold-arm-1.records.csv", ROOT / "data/reading/records.csv"],
    "vlm": [ROOT / "data/arm/eval/arm-gold-arm-1-vlm.records.csv", ROOT / "data/reading/records-vlm.csv"],
}
CONFIGS = {"ocr": "OCR", "vlm": "OCR и модель"}
GRID = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.93, 0.95, 0.96, 1.01)
PARTS = {"Основная надпись": "основные надписи", "Строка спецификации": "строки спецификаций"}
SEED = 20261121


@dataclass(frozen=True)
class Value:
    document: str  # corpus/kit/document
    part: str
    method: str
    confidence: float | None
    conflict: bool
    right: bool

    @property
    def half(self) -> int:
        return int(hashlib.sha256(self.document.encode()).hexdigest()[:8], 16) % 2

    def marked(self, threshold: float) -> bool:
        low = self.confidence is None or self.confidence < threshold
        return self.method == "filename" or self.conflict or low


def load(path: Path) -> list[Value]:
    values = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["корпус"] == "stress" or not row["прочитано"].strip() or row["вход и класс"] in ("вектор", "vector"):
                continue
            label = row["поле"]
            values.append(
                Value(
                    document=f"{row['корпус']}/{row['комплект']}/{row['документ']}",
                    part=next((v for k, v in PARTS.items() if label.startswith(k)), "пункты ТТ"),
                    method=row["движок"].split(":")[0],
                    confidence=float(row["уверенность"]) if row["уверенность"] else None,
                    conflict=row["статус"] == "conflict",
                    right=row["верно"] == "True",
                )
            )
    return values


def measure(values: list[Value], threshold: float) -> dict[str, Any]:
    marked = [v for v in values if v.marked(threshold)]
    unmarked = [v for v in values if not v.marked(threshold)]
    wrong = [v for v in values if not v.right]
    return {
        "values": len(values),
        "marked": len(marked),
        "unmarked": len(unmarked),
        "unmarked_wrong": sum(not v.right for v in unmarked),
        "wrong": len(wrong),
        "wrong_marked": sum(v.marked(threshold) for v in wrong),
    }


def interval(values: list[Value], threshold: float, rounds: int = 2000) -> tuple[float, float] | None:
    """95 % bootstrap interval of the share of wrong values among the unmarked, resampling documents; none when every
    value is marked."""
    by_doc: dict[str, list[Value]] = {}
    for v in values:
        by_doc.setdefault(v.document, []).append(v)
    docs = list(by_doc.values())
    rng = random.Random(SEED)
    shares = []
    for _ in range(rounds):
        sample = [v for _ in docs for v in rng.choice(docs) if not v.marked(threshold)]
        if sample:
            shares.append(sum(not v.right for v in sample) / len(sample))
    if not shares:
        return None
    shares.sort()
    return shares[int(0.025 * len(shares))], shares[int(0.975 * len(shares)) - 1]


def calibrate(values: list[Value], target: float) -> dict[str, Any]:
    tune = [v for v in values if v.half == 0]
    check = [v for v in values if v.half == 1]

    def fits(threshold: float) -> bool:
        m = measure(tune, threshold)
        return m["unmarked"] > 0 and m["unmarked_wrong"] <= target * m["unmarked"]

    threshold = next((t for t in GRID if fits(t)), GRID[-1])
    return {
        "threshold": threshold,
        "tune": measure(tune, threshold),
        "check": measure(check, threshold),
        "check_interval": interval(check, threshold),
        "check_by_part": {
            p: measure([v for v in check if v.part == p], threshold) for p in sorted({v.part for v in check})
        },
        "check_by_source": {
            s: measure([v for v in check if v.document.startswith(s)], threshold) for s in ("arm", "synthetic")
        },
        "grid": {f"{t:.2f}": measure(values, t) for t in GRID},
    }


def _share(a: int, b: int) -> str:
    return f"{a / b:.3f}".replace(".", ",") if b else "—"


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Отметка «проверить»: калибровка порога уверенности",
        "",
        f"Получено `uv run python eval/review_calibration.py --target {result['target']}`. Значение отмечено, если оно "
        "взято из имени файла, если два прочтения разошлись (статус `conflict`) или если его уверенность ниже порога. "
        "Считаются значения растров и сканов, которые отчёт дал: основные надписи, строки спецификаций, пункты ТТ "
        "комплекта `arm-1` ([arm-gold-arm-1.md](arm-gold-arm-1.md)) и замороженного синтетического корпуса "
        "([reading-errors.md](reading-errors.md)) без стрессовой части. Документы поделены пополам по sha256 корпуса, "
        "комплекта и имени: на первой половине выбран наименьший порог сетки, при котором среди неотмеченных "
        f"значений неверных не больше {result['target']:.0%}; вторая половина его проверяет. Интервал — 95 % "
        "бутстрепом по документам второй половины. До калибровки отмечено каждое значение OCR и модели.",
        "",
        "| Чтение | Порог | Значений | Отмечено | Неверных среди неотмеченных | Интервал | Неверных отмечено |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for config, r in result["configs"].items():
        c, bounds = r["check"], r["check_interval"]
        spread = f"{bounds[0]:.3f}–{bounds[1]:.3f}" if bounds else "—"
        lines.append(
            f"| {CONFIGS[config]} | {r['threshold']:.2f} | {c['values']} | {c['marked']} "
            f"({_share(c['marked'], c['values'])}) | {c['unmarked_wrong']} из {c['unmarked']} "
            f"({_share(c['unmarked_wrong'], c['unmarked'])}) | {spread} | "
            f"{c['wrong_marked']} из {c['wrong']} |"
        )
    for config, r in result["configs"].items():
        lines += [
            "",
            f"## {CONFIGS[config]}: проверочная половина по частям и источникам",
            "",
            "| Что | Значений | Отмечено | Неверных среди неотмеченных | Неверных отмечено |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for name, m in [
            *r["check_by_part"].items(),
            *(("arm-1" if s == "arm" else "синтетика", m) for s, m in r["check_by_source"].items()),
        ]:
            lines.append(
                f"| {name} | {m['values']} | {m['marked']} | {m['unmarked_wrong']} из {m['unmarked']} | "
                f"{m['wrong_marked']} из {m['wrong']} |"
            )
        lines += [
            "",
            "Все документы, по порогам сетки:",
            "",
            "| Порог | Отмечено | Неверных среди неотмеченных |",
            "| ---: | ---: | ---: |",
        ]
        for t, m in r["grid"].items():
            lines.append(
                f"| {t} | {_share(m['marked'], m['values'])} | {m['unmarked_wrong']} из {m['unmarked']} "
                f"({_share(m['unmarked_wrong'], m['unmarked'])}) |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--target", type=float, default=0.02, help="доля неверных среди неотмеченных значений")
    args = parser.parse_args(argv)
    result: dict[str, Any] = {"target": args.target, "configs": {}}
    for config, paths in SOURCES.items():
        values = [v for path in paths for v in load(path)]
        result["configs"][config] = calibrate(values, args.target)
    OUT.mkdir(exist_ok=True)
    (OUT / "review-calibration.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "review-calibration.md").write_text(markdown(result) + "\n", encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
