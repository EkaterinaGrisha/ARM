"""Оценка качества отчёта по эталонной разметке.

    python -m arm_web.evaluate report.json truth.json [--write]

Формат эталона — как у ``<комплект>_truth.json``, который пишет генератор демо-комплекта:
ключи ``documents`` (по пути файла: тип, поля основной надписи, параметры),
``discrepancies`` (тип + обозначение) и ``problem_files`` (путь → статус).
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from .labels import PARAMETER_CATEGORY
from .models import ErrorCase, QualityMetric, QualityReport, Report

TITLE_FIELDS = {
    "designation": "Обозначение",
    "name": "Наименование",
    "material": "Материал",
    "mass": "Масса",
    "scale": "Масштаб",
    "litera": "Литера",
    "revision": "Номер изменения",
    "archive_number": "Архивный номер",
    "first_use": "Первое применение",
}
TYPE_NAMES = {
    "missing_drawing": "Отсутствует чертёж",
    "orphan_drawing": "Чертёж вне спецификаций",
    "name_mismatch": "Расхождение наименования",
    "duplicate_designation": "Дублирование обозначения",
    "revision_mismatch": "Литера / номер изменения",
    "missing_specification": "Нет спецификации СЕ",
}


def _norm(v) -> str:
    if v is None:
        return ""
    s = str(v).lower().replace("ё", "е").replace(",", ".").replace("ø", "").replace("⌀", "")
    return re.sub(r"\s+", "", s)


def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return (round(p, 3) if p is not None else None, round(r, 3) if r is not None else None,
            round(f, 3) if f is not None else None)


def evaluate(report: Report, truth: dict, dataset: str = "") -> QualityReport:
    q = QualityReport(dataset=dataset)
    by_path = {d.path: d for d in report.documents}
    parts = {p.document_id: p for p in report.parts}

    # ---------------------------------------------------------------- блок 1: расхождения
    expected = {(d["type"], d["designation"]) for d in truth.get("discrepancies", [])}
    found = {(d.type, d.designation) for d in report.discrepancies}
    for t in TYPE_NAMES:
        e = {x for x in expected if x[0] == t}
        f = {x for x in found if x[0] == t}
        if not e and not f:
            continue
        p, r, f1 = _prf(len(e & f), len(f - e), len(e - f))
        q.metrics.append(QualityMetric(block="completeness", name=TYPE_NAMES[t], precision=p, recall=r, f1=f1, support=len(e)))
    p, r, f1 = _prf(len(expected & found), len(found - expected), len(expected - found))
    q.metrics.insert(0, QualityMetric(block="completeness", name="Расхождения — всего", precision=p, recall=r, f1=f1,
                                      support=len(expected)))
    for t, des in sorted(found - expected, key=str):
        q.errors.append(ErrorCase(block="completeness", kind="false_positive", title=f"{TYPE_NAMES.get(t, t)}: {des}",
                                  description="Срабатывание отсутствует в эталоне"))
    for t, des in sorted(expected - found, key=str):
        q.errors.append(ErrorCase(block="completeness", kind="false_negative", title=f"{TYPE_NAMES.get(t, t)}: {des}",
                                  description="Расхождение из эталона не найдено"))

    problems = truth.get("problem_files", {})
    if problems:
        ok = sum(1 for path, status in problems.items() if path in by_path and by_path[path].status == status)
        q.metrics.append(QualityMetric(block="completeness", name="Классификация проблемных файлов",
                                       accuracy=round(ok / len(problems), 3), support=len(problems)))
        for path, status in problems.items():
            got_status = by_path[path].status if path in by_path else "нет в отчёте"
            if got_status != status:
                q.errors.append(ErrorCase(block="completeness", kind="wrong_value", title=f"Статус файла {path}",
                                          description=f"ожидалось «{status}», получено «{got_status}»",
                                          document_id=by_path[path].id if path in by_path else None))

    # ---------------------------------------------------------------- блок 2: основные надписи
    field_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    origin_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    param_tp = param_fp = param_fn = 0
    for path, t in truth.get("documents", {}).items():
        doc = by_path.get(path)
        if t.get("kind") == "specification":
            continue
        part = parts.get(doc.id) if doc else None
        origin = t.get("origin", "vector")
        for key, title in TITLE_FIELDS.items():
            want = t.get(key)
            if want in (None, ""):
                continue
            ex = getattr(part.title_block, key, None) if part else None
            got = ex.value if ex is not None else None
            hit = _norm(got) == _norm(want)
            field_stats[key][0] += hit
            field_stats[key][1] += 1
            origin_stats[origin][0] += hit
            origin_stats[origin][1] += 1
            if not hit:
                q.errors.append(ErrorCase(block="extraction", kind="wrong_value", title=f"{title}: {path}",
                                          description=f"ожидалось «{want}», получено «{got if got is not None else '—'}»",
                                          document_id=doc.id if doc else None))
        # параметры ТТ — сопоставление один к одному по категории и имени
        want_params = list(t.get("parameters", []))
        got_params = list(part.parameters) if part else []
        used: set[int] = set()
        for wp in want_params:
            match = None
            for i, gp in enumerate(got_params):
                if i in used or gp.category != wp["category"]:
                    continue
                hay = _norm(f"{gp.name} {gp.qualifier or ''} {gp.text}")
                if _norm(wp["name"]) in hay:
                    match = i
                    break
            if match is None:
                param_fn += 1
                q.errors.append(ErrorCase(block="extraction", kind="false_negative", title=f"Параметр не извлечён: {wp['name']}",
                                          description=f"{path}; категория «{PARAMETER_CATEGORY.get(wp['category'], wp['category'])}»", document_id=doc.id if doc else None))
            else:
                used.add(match)
                param_tp += 1
        for i, gp in enumerate(got_params):
            if i not in used:
                param_fp += 1
                q.errors.append(ErrorCase(block="extraction", kind="false_positive", title=f"Лишний параметр: {gp.name}",
                                          description=f"{path}: «{gp.text}»", document_id=doc.id if doc else None))
    for key, title in TITLE_FIELDS.items():
        correct, total = field_stats.get(key, [0, 0])
        if total:
            q.metrics.append(QualityMetric(block="extraction", name=f"Основная надпись: {title}",
                                           accuracy=round(correct / total, 3), support=total))
    for origin, title in (("vector", "векторные PDF"), ("scan", "сканы")):
        correct, total = origin_stats.get(origin, [0, 0])
        if total:
            q.metrics.append(QualityMetric(block="extraction", name=f"Основная надпись — {title}",
                                           accuracy=round(correct / total, 3), support=total))
    p, r, f1 = _prf(param_tp, param_fp, param_fn)
    q.metrics.append(QualityMetric(block="extraction", name="Параметры технических требований", precision=p, recall=r,
                                   f1=f1, support=param_tp + param_fn))

    # ---------------------------------------------------------------- спецификации
    rows_hit = rows_total = 0
    specs_by_doc = {s.document_id: s for s in report.specifications}
    for path, t in truth.get("documents", {}).items():
        if t.get("kind") != "specification":
            continue
        doc = by_path.get(path)
        spec = specs_by_doc.get(doc.id) if doc else None
        got_rows = spec.rows if spec else []
        for want in t.get("rows", []):
            rows_total += 1
            key = want["designation"] or want["name"]
            for gr in got_rows:
                gkey = (gr.designation.value if gr.designation else None) or (gr.name.value if gr.name else None)
                if _norm(gkey) == _norm(key):
                    same = (_norm(gr.name.value if gr.name else "") == _norm(want["name"])
                            and _norm(gr.quantity.value if gr.quantity else "") in (_norm(want["qty"]), _norm(want["qty"]) + ".0")
                            and (gr.section or "") == want["section"])
                    rows_hit += same
                    break
            else:
                q.errors.append(ErrorCase(block="extraction", kind="false_negative", title=f"Строка спецификации: {key}",
                                          description=path, document_id=doc.id if doc else None))
    if rows_total:
        q.metrics.append(QualityMetric(block="extraction", name="Строки спецификаций (раздел, обозначение, наименование, кол.)",
                                       accuracy=round(rows_hit / rows_total, 3), support=rows_total))

    q.methodology = (
        "Эталон — параметры генерации комплекта: для каждого документа известны тип, поля основной надписи, "
        "пункты ТТ и обозначения на поле чертежа, для комплекта — заложенные расхождения и проблемные файлы. "
        "Расхождения сопоставляются по паре (тип, обозначение); поля основной надписи — по точному совпадению "
        "после нормализации регистра, пробелов, «ё/е» и десятичного разделителя; параметры ТТ — один к одному "
        "по категории и имени (Ra 0,8; Ø40h6; HRC…). Пропущенное значение считается ошибкой."
    )
    q.limitations = [
        "Синтетический комплект проще реального: единый шрифт, стандартная геометрия форм, без рукописных правок.",
        "Сканы без OCR обрабатываются только по имени файла; качество OCR зависит от разрешения и шрифта.",
        "Параметры, нанесённые на поле чертежа в рамках (допуски формы) и многострочные допуски, распознаются "
        "только при наличии текстового слоя.",
        "Правила проверки литеры (ГОСТ 2.103) и отбора критичных параметров требуют согласования с технологами АРМ.",
    ]
    return q


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print(__doc__)
        return 2
    report_path, truth_path = Path(argv[0]), Path(argv[1])
    report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    quality = evaluate(report, truth, dataset=truth_path.name)
    for m in quality.metrics:
        vals = " ".join(f"{k}={getattr(m, k)}" for k in ("precision", "recall", "f1", "accuracy") if getattr(m, k) is not None)
        print(f"[{m.block}] {m.name}: {vals} (n={m.support})")
    if "--write" in argv:
        report.quality = quality
        report_path.write_text(report.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
