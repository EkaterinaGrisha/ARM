"""Title blocks of scans with a vision-language model and without it (ADR-0014).

Runs the baseline pipeline with OCR on the demo kit and on synthetic kits of ``synth.kits`` built from a seed into
``data/synth/vlm_kits`` (outside git; clean kits, half of the drawings scanned). First without the model, then with
each model given, and compares the title block fields of the scanned drawings with the truth: right, wrong and
missing per field, and how many values the model gave and how many of them are right. ``--verify MODEL`` adds a run
where that model also reads the cells OCR filled; the conflicts are counted with the reading the truth supports.

The models run on an OpenAI-compatible server on this machine, LM Studio by default; the server must have them.
Writes eval/results/vlm-title-block.json and eval/results/vlm-title-block.md. ``--real`` measures the real sheets of
the gold set instead (eval/gold/sheets.json, cut into data/gold_sheets by ``eval/gold.py sheets``) and writes
eval/results/vlm-title-block-real.json and .md, with a table by sheet. A field the truth leaves empty or the blank lacks
is not scored, as in backend/arm_web/evaluate.py.

    uv run python eval/vlm_title_block.py --model google/gemma-4-e4b --model qwen/qwen3.6-27b \
        [--verify google/gemma-4-e4b] [--real] [--append]

``--append`` keeps the runs of the earlier results file, replaces the runs of the models given and adds the new
ones; the command written to the results is the union of both. ``--cache`` keeps the readings of OCR and the models in
data/cache (ADR-0018): a run gives back what an earlier one read, so its time is no time of reading.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import shutil
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kdcheck import cache as read_cache
from kdcheck.config import Settings
from kdcheck.pipeline import BaselinePipeline
from kdcheck.run import load_settings, ocr_available
from kdcheck.schemas import Report
from scoring import norm
from synth.truth import DrawingTruth, KitTruth

from synth import kits

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "samples" / "demo_kit"
CORPUS = ROOT / "data" / "synth" / "vlm_kits"
CACHE = ROOT / "data" / "cache"
SHEETS = ROOT / "data" / "gold_sheets"
GOLD = ROOT / "eval" / "gold"
OUT = Path(__file__).resolve().parent / "results"
FIELDS = ("designation", "name", "material", "mass", "scale", "litera")
UNREAD = re.compile(r"Ячейку «[^»]+» модель не прочитала: (?P<why>.+)")
CONFLICT = re.compile(r"«(?P<label>[^»]+)»: прочитано «(?P<ocr>[^»]*)», модель прочла «(?P<vlm>[^»]*)»")


def settings_for(model: str | None, url: str, verify: bool) -> Settings:
    settings = load_settings(use_ocr=True, use_vlm=model is not None)
    if model is None:
        return settings
    vlm = settings.models.vlm.model_copy(update={"base_url": url, "model": model, "verify": verify})
    return settings.model_copy(update={"models": settings.models.model_copy(update={"vlm": vlm})})


def score(report: Report, truth: KitTruth, labels: dict[str, str], tally: dict[str, Counter[str]]) -> None:
    ids = {d.path: d.id for d in report.documents}
    parts = {p.document_id: p for p in report.parts}
    notes = {d.id: d.notes for d in report.documents}
    by_label = {label: field for field, label in labels.items()}
    for path, doc in truth.documents.items():
        if not isinstance(doc, DrawingTruth) or doc.origin != "scan":
            continue
        tally["drawings"]["scans"] += 1
        part = parts.get(ids.get(path, ""))
        tb = part.title_block if part else None
        sheet = tally.setdefault(f"sheet:{path}", Counter())
        for field in FIELDS:
            want = getattr(doc, field)
            if want in (None, ""):
                continue
            got = getattr(tb, field) if tb else None
            counts = tally[field]
            counts["total"] += 1
            sheet["total"] += 1
            if got is None or got.value in (None, ""):
                counts["missing"] += 1
                continue
            right = norm(got.value) == norm(want)
            counts["right" if right else "wrong"] += 1
            sheet["right"] += right
            if got.source and got.source.method == "vlm":
                counts["by_model"] += 1
                counts["by_model_right"] += right
        for note in notes.get(ids.get(path, ""), []):
            m = CONFLICT.search(note)
            if m and m["label"] in by_label:
                want = getattr(doc, by_label[m["label"]])
                model_right, kept_right = norm(m["vlm"]) == norm(want), norm(m["ocr"]) == norm(want)
                tally["conflicts"]["total"] += 1
                tally["conflicts"]["model_right"] += model_right
                tally["conflicts"]["kept_right"] += kept_right
                if want not in (None, ""):  # a scored field: the modes below change its value
                    tally["conflicts"]["scored"] += 1
                    tally["conflicts"]["scored_model_right"] += model_right
                    tally["conflicts"]["scored_kept_right"] += kept_right


def run(name: str, model: str | None, url: str, verify: bool, truths: dict[Path, KitTruth]) -> dict[str, Any]:
    settings = settings_for(model, url, verify)
    pipeline = BaselinePipeline(settings)
    tally: dict[str, Counter[str]] = {key: Counter() for key in (*FIELDS, "drawings", "conflicts")}
    started = time.monotonic()
    problems = []
    failed: Counter[str] = Counter()  # the cells the model did not read, by the reason
    for folder, truth in truths.items():
        report = pipeline.run(folder, kit_id=folder.name, kit_name=folder.name)
        problems += [e.message for e in report.log if "Модель" in e.message]
        failed.update(m["why"] for d in report.documents for note in d.notes if (m := UNREAD.match(note)))
        score(report, truth, dict(settings.models.vlm.fields), tally)
    seconds = round(time.monotonic() - started, 1)
    calls = pipeline.vlm.calls if pipeline.vlm else 0
    if calls and failed.total() >= calls:
        # A run where the model answered nothing is no measurement: the server, say, had no memory to load it.
        why = failed.most_common(1)[0][0]
        raise RuntimeError(f"{name}: модель не прочитала ни одной ячейки из {calls} — {why}")
    problems += [f"Модель не прочитала ячеек: {n} — {why}" for why, n in failed.most_common()]
    fields = {}
    for field in FIELDS:
        c = tally[field]
        fields[field] = {
            "total": c["total"],
            "right": c["right"],
            "wrong": c["wrong"],
            "missing": c["missing"],
            "accuracy": round(c["right"] / c["total"], 3) if c["total"] else None,
            "by_model": c["by_model"],
            "by_model_right": c["by_model_right"],
        }
    total = sum(f["total"] for f in fields.values())
    right = sum(f["right"] for f in fields.values())
    result = {
        "name": name,
        "model": model,
        "prompt": pipeline.vlm.version if pipeline.vlm else None,
        "verify": verify,
        "scans": tally["drawings"]["scans"],
        "accuracy": round(right / total, 3) if total else None,
        "fields": fields,
        "model_values": sum(f["by_model"] for f in fields.values()),
        "model_values_right": sum(f["by_model_right"] for f in fields.values()),
        "model_calls": pipeline.vlm.calls if pipeline.vlm else 0,
        "conflicts": dict(tally["conflicts"]),
        "seconds": seconds,
        "problems": sorted(set(problems)),
        "sheets": {
            key.removeprefix("sheet:"): {"right": c["right"], "total": c["total"]}
            for key, c in sorted(tally.items())
            if key.startswith("sheet:")
        },
    }
    if verify and total:
        result["modes"] = modes(fields, tally["conflicts"], total, right)
    return result


def modes(fields: dict[str, dict[str, Any]], conflicts: Counter[str], total: int, right: int) -> dict[str, Any]:
    """What a verify run gives under each policy for a conflict, counted on the scored fields.

    ``fill_empty`` keeps the OCR value, as the pipeline does; ``model_wins`` takes the model's; ``review`` keeps the
    OCR value and marks it for a check. ``agreement``: OCR filled the cell and the model read the same.
    """
    wrong = sum(f["wrong"] for f in fields.values())
    scored, model_right, kept_right = (conflicts[k] for k in ("scored", "scored_model_right", "scored_kept_right"))
    by_model = sum(f["by_model"] for f in fields.values())
    by_model_right = sum(f["by_model_right"] for f in fields.values())
    agreed = right + wrong - by_model - scored
    return {
        "fill_empty": {"accuracy": round(right / total, 3), "unflagged_wrong": wrong, "flagged": 0},
        "model_wins": {
            "accuracy": round((right - kept_right + model_right) / total, 3),
            "unflagged_wrong": wrong + kept_right - model_right,
            "flagged": 0,
        },
        "review": {
            "accuracy": round(right / total, 3),
            "unflagged_wrong": wrong - scored + kept_right,
            "flagged": scored,
        },
        "agreement": {"total": agreed, "right": right - by_model_right - kept_right},
    }


def corpus(count: int, seed: int) -> dict[Path, KitTruth]:
    shutil.rmtree(CORPUS, ignore_errors=True)
    CORPUS.mkdir(parents=True)
    built = kits.corpus(CORPUS, count, seed, clean_every=1, scan_share=0.5)
    truths = {DEMO: KitTruth.read(ROOT / "samples" / "demo_kit_truth.json")}
    truths.update({CORPUS / f"kit_{i:03d}": t for i, t in enumerate(built, start=1)})
    return truths


def sheet_inputs() -> dict[str, str]:
    """How each sheet of the gold set reaches the pipeline: a PDF with the archive's OCR layer, a picture or a photo."""
    with (ROOT / "eval" / "open_drawings.csv").open(encoding="utf-8", newline="") as f:
        quality = {row["path"]: row["quality"] for row in csv.DictReader(f)}
    with (GOLD / "sheets.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    kinds = {"pdf": "PDF со слоем распознавания", "jpg": "картинка"}
    return {
        row["sheet"]: "фото" if quality.get(row["source"]) == "photo" else kinds.get(row["form"], row["form"])
        for row in rows
    }


def markdown(result: dict[str, Any]) -> str:
    env = result["environment"]
    scans = result["runs"][0]["scans"]
    if result["real"]:
        title = "# Основная надпись реальных сканов: OCR и мультимодальная модель"
        where = (
            f"Корпус: листы эталона ([eval/gold/README.md](../gold/README.md#листы-с-основной-надписью)), {scans} "
            "сканов чертежей — альбом ВЛ60 1963 года, фото ИЖ-250, альбомы приборов; полностью — "
            "[vlm-title-block-real.json](vlm-title-block-real.json)."
        )
    else:
        title = "# Основная надпись сканов: OCR и мультимодальная модель"
        where = (
            f"Корпус: демо-комплект и {result['kits']} синтетических комплектов (сид {result['seed']}, половина "
            f"чертежей — сканы), всего {scans} сканов чертежей; полностью — "
            "[vlm-title-block.json](vlm-title-block.json)."
        )
    lines = [
        title,
        "",
        f"Получено `uv run python eval/vlm_title_block.py {result['command']}` {result['date']}; "
        f"{env['machine']}, Python {env['python']}, Tesseract {env['tesseract']}; сервер моделей {result['url']}.",
        where
        + (
            " Прочтения OCR и моделей взяты из кэша, где они были (ADR-0018): время прогона — не время чтения."
            if result.get("cache")
            else ""
        ),
        "Поля: обозначение, наименование, материал, масса, масштаб, литера; поле, которое в эталоне пусто или которого "
        "нет на бланке, не считается. Сравнение — по правилу верного извлечения "
        "([eval/gold/README.md](../gold/README.md#что-считается-верным-извлечением)). Модель читает только ячейки, "
        "которые OCR оставил пустыми"
        + (", а с проверкой — и заполненные" if any(r["verify"] for r in result["runs"]) else "")
        + ".",
        "",
        "| Прогон | Точность | Значений от модели | Из них верных | Обращений к модели | Конфликтов | Время, с |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in result["runs"]:
        conflicts = r["conflicts"].get("total", 0)
        lines.append(
            f"| {r['name']} | {r['accuracy']} | {r['model_values']} | {r['model_values_right']} | "
            f"{r['model_calls']} | {conflicts} | {r['seconds']} |"
        )
    lines += ["", "По полям — верно из всех, в скобках пропусков:", ""]
    lines.append("| Прогон | " + " | ".join(FIELDS) + " |")
    lines.append("| --- |" + " ---: |" * len(FIELDS))
    for r in result["runs"]:
        cells = [f"{f['right']}/{f['total']} ({f['missing']})" for f in r["fields"].values()]
        lines.append(f"| {r['name']} | " + " | ".join(cells) + " |")
    verify = [r for r in result["runs"] if r["verify"] and r["conflicts"].get("total")]
    if verify:
        lines += ["", "Конфликты при проверке: чьё прочтение совпало с эталоном.", ""]
        lines += ["| Прогон | Конфликтов | Модель права | OCR прав |", "| --- | ---: | ---: | ---: |"]
        for r in verify:
            c = r["conflicts"]
            lines.append(f"| {r['name']} | {c['total']} | {c.get('model_right', 0)} | {c.get('kept_right', 0)} |")
    with_modes = [r for r in result["runs"] if r.get("modes")]
    if with_modes:
        lines += [
            "",
            "Режимы при расхождении OCR и модели, по полям эталона: «дочитывание» оставляет OCR, как конвейер сейчас; "
            "«модель» берёт значение модели; «проверить» оставляет OCR и отмечает поле. «Неверных без пометки» — "
            "неверные значения, которые отчёт выдаст как есть.",
            "",
            "| Прогон | Режим | Доля верных полей | Неверных без пометки | Отмечено «проверить» |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        names = {"fill_empty": "дочитывание", "model_wins": "модель", "review": "проверить"}
        for r in with_modes:
            for key, label in names.items():
                m = r["modes"][key]
                lines.append(f"| {r['name']} | {label} | {m['accuracy']} | {m['unflagged_wrong']} | {m['flagged']} |")
        lines += ["", "Поля, где OCR прочёл значение и модель с ним не разошлась: верно из всех.", ""]
        lines += ["| Прогон | Верно | Всего |", "| --- | ---: | ---: |"]
        for r in with_modes:
            a = r["modes"]["agreement"]
            lines.append(f"| {r['name']} | {a['right']} | {a['total']} |")
    if result["real"]:
        inputs = result["inputs"]
        lines += ["", "По листам — верно из полей, которые есть на бланке:", ""]
        lines.append("| Лист | Вход | " + " | ".join(r["name"] for r in result["runs"]) + " |")
        lines.append("| --- | --- |" + " ---: |" * len(result["runs"]))
        for sheet in result["runs"][0]["sheets"]:
            cells = [f"{r['sheets'][sheet]['right']}/{r['sheets'][sheet]['total']}" for r in result["runs"]]
            lines.append(f"| {sheet} | {inputs.get(sheet, '')} | " + " | ".join(cells) + " |")
    problems = sorted({p for r in result["runs"] for p in r["problems"]})
    if problems:
        lines += ["", "Сообщения прогона:", "", *[f"- {p}" for p in problems]]
    lines += [
        "",
        "Листов мало: разница в несколько значений между прогонами — не разница моделей. Решение включать модель по "
        "умолчанию — только по замеру на эталонной выборке (этап 8)."
        if result["real"]
        else "Сканы синтетические: шрифт и деградации свои (ADR-0013), реальные сканы АРМ могут дать другие цифры. "
        "Решение включать модель по умолчанию — только по замеру на эталонной выборке (этап 8).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--model", action="append", default=[], help="модель на сервере; можно несколько")
    parser.add_argument("--url", default="http://127.0.0.1:1234/v1", help="OpenAI-совместимый сервер моделей")
    parser.add_argument(
        "--verify",
        action="append",
        default=[],
        help="модель, которая в отдельном прогоне читает и ячейки, заполненные OCR",
    )
    parser.add_argument("--kits", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--real", action="store_true", help="листы эталона из data/gold_sheets вместо синтетики")
    parser.add_argument(
        "--append",
        action="store_true",
        help="оставить прогоны прежнего файла результатов и добавить или заменить прогоны этих моделей",
    )
    parser.add_argument("--cache", action="store_true", help="брать и хранить прочтения в data/cache (ADR-0018)")
    args = parser.parse_args(argv)
    if not ocr_available(load_settings(use_ocr=True)):
        parser.error("нужен Tesseract с языками rus+eng: замер сравнивает модель с OCR")
    if args.real and not SHEETS.is_dir():
        parser.error("нет data/gold_sheets: листы вырезает uv run python eval/gold.py sheets")
    name = "vlm-title-block-real" if args.real else "vlm-title-block"
    earlier = json.loads((OUT / f"{name}.json").read_text(encoding="utf-8")) if args.append else None
    if earlier is not None and (earlier["kits"], earlier["seed"]) != (args.kits, args.seed):
        parser.error("--append: у прежних результатов другие --kits или --seed")
    truths = {SHEETS: KitTruth.read(GOLD / "sheets.json")} if args.real else corpus(args.kits, args.seed)
    with read_cache.use(CACHE if args.cache else None):
        runs = [run("OCR", None, args.url, False, truths)]
        for model in args.model:
            runs.append(run(f"OCR + {model}", model, args.url, False, truths))
        for model in args.verify:
            runs.append(run(f"OCR + {model}, проверка", model, args.url, True, truths))
    models, verify = list(args.model), list(args.verify)
    if earlier is not None:
        # The runs are deterministic, so the union of both commands reproduces the merged file.
        flags = re.findall(r"--(model|verify) (\S+)", earlier["command"])
        models = [*(m for flag, m in flags if flag == "model" and m not in models), *models]
        verify = [*(m for flag, m in flags if flag == "verify" and m not in verify), *verify]
        fresh = {r["name"]: r for r in runs}
        runs = [fresh.pop(r["name"], r) for r in earlier["runs"]] + list(fresh.values())
    tesseract = next(
        (e.version for e in BaselinePipeline(load_settings(use_ocr=True)).engines() if e.name == "tesseract"), None
    )
    result = {
        "command": " ".join(
            [
                *(f"--model {m}" for m in models),
                *(f"--verify {m}" for m in verify),
                *(["--real"] if args.real else []),
                *(["--cache"] if args.cache else []),
            ]
        ),
        "date": datetime.now(UTC).date().isoformat(),
        "url": args.url,
        "real": args.real,
        "cache": args.cache,
        "cache_stats": dict(read_cache.stats),
        "inputs": sheet_inputs() if args.real else {},
        "kits": args.kits,
        "seed": args.seed,
        "environment": {
            "machine": f"{platform.system()} {platform.machine()}",
            "python": platform.python_version(),
            "tesseract": tesseract,
        },
        "runs": runs,
    }
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / f"{name}.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
