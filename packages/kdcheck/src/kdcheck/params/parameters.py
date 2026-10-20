"""Parameters from technical requirements and drawing field annotations.

Results share one :class:`Parameter` structure (category, name, numeric values, units, source text and location)
that compares across parts and loads into accounting systems.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import Line
from kdcheck.schemas import Extracted, Parameter, SourceRef

NextId = Callable[[], str]

NUM = r"\d+(?:[.,]\d+)?"


def _f(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


HEAT_TREATMENTS = [
    (r"цементир|цементац", "Цементация"),
    (r"нитроцемент", "Нитроцементация"),
    (r"азотир", "Азотирование"),
    (r"(закал\w*|закалк\w*)\s+ТВЧ|ТВЧ", "Закалка ТВЧ"),
    (r"закал", "Закалка"),
    (r"улучш", "Улучшение"),
    (r"нормализ", "Нормализация"),
    (r"отжиг|отжечь", "Отжиг"),
    (r"старени|состарить", "Старение"),
]
HARDNESS = re.compile(
    rf"(?P<a>{NUM})\s?(?:\.{{2,3}}|…|-|–)\s?(?P<b>{NUM})\s?(?P<u>HRC[эЭ]?|HB|HV|НВ|НRС)"
    rf"|(?P<u2>HRC[эЭ]?|HB|HV|НВ)\s?(?P<c>{NUM})\s?(?:\.{{2,3}}|…|-|–)\s?(?P<d>{NUM})"
)
DEPTH = re.compile(rf"h\s?(?P<a>{NUM})\s?(?:\.{{2,3}}|…|-|–)\s?(?P<b>{NUM})\s?мм")
COATING_HINT = re.compile(
    r"покрыти|хим\.?\s?окс|ан\.?\s?окс|\bЦ\d|\bКд\d|\bН\d+\.|\bХ\d|фосф|грунт|эмаль|лак|окрасить|оксидир",
    re.IGNORECASE,
)
GENERAL_TOL = re.compile(r"(?i)неуказанные\s+предельные\s+отклонения")
IT = re.compile(r"IT\s?(\d{1,2})")
GEOMETRIC_TEXT = [
    (r"позиционн", "Позиционный допуск"),
    (r"плоскостност", "Допуск плоскостности"),
    (r"параллельност", "Допуск параллельности"),
    (r"перпендикулярност", "Допуск перпендикулярности"),
    (r"соосност", "Допуск соосности"),
    (r"симметричност", "Допуск симметричности"),
    (r"радиальн\w+\s+биени", "Допуск радиального биения"),
    (r"торцов\w+\s+биени", "Допуск торцового биения"),
    (r"цилиндричност", "Допуск цилиндричности"),
    (r"круглост", "Допуск круглости"),
]
ROUGHNESS = re.compile(rf"(?<![A-Za-zА-Яа-я])(?P<p>[RРВК][aаzг])\s?(?P<v>{NUM})")


def parse_requirement(item: Extracted, next_id: NextId) -> list[Parameter]:
    text = str(item.value or "")
    low = text.lower()
    params: list[Parameter] = []
    src = item.source

    def add(**kw: Any) -> None:
        params.append(Parameter(id=next_id(), text=text, source=src, confidence=src.confidence if src else None, **kw))

    if GENERAL_TOL.search(text):
        m = IT.search(text)
        grade = int(m.group(1)) if m else None
        fits = re.findall(r"\b[HhJjs]{1,2}\d{1,2}\b", text)
        add(
            category="general_tolerance",
            name=f"IT{grade}" if grade else "Неуказанные отклонения",
            it_grade=grade,
            qualifier=", ".join(fits) or None,
            general=True,
        )
        return params
    for pattern, name in HEAT_TREATMENTS:
        if re.search(pattern, text, re.IGNORECASE):
            d = DEPTH.search(text)
            add(
                category="heat_treatment",
                name=name,
                value_min=_f(d["a"]) if d else None,
                value_max=_f(d["b"]) if d else None,
                unit="мм" if d else None,
                qualifier=name,
            )
            break
    for m in HARDNESS.finditer(text):
        unit = (m["u"] or m["u2"]).replace("НВ", "HB").replace("НRС", "HRC")
        lo, hi = _f(m["a"] or m["c"]), _f(m["b"] or m["d"])
        add(category="hardness", name=f"Твёрдость {unit}", value_min=lo, value_max=hi, unit=unit, qualifier=unit)
    if COATING_HINT.search(text):
        coat = re.split(r"(?i)покрытие[^:]*?:|покрытие", text, maxsplit=1)
        value = (coat[1] if len(coat) > 1 else text).strip(" .:")
        add(category="coating", name=value[:80], qualifier=value)
    for pattern, name in GEOMETRIC_TEXT:
        if re.search(pattern, low):
            m = re.search(rf"(?:Ø|⌀)?\s?(?P<v>{NUM})\s?мм", text)
            add(category="geometric", name=name, value=_f(m["v"]) if m else None, unit="мм" if m else None)
            break
    for m in ROUGHNESS.finditer(text):
        params.append(_roughness(m, text, src, next_id, general=False))
    return params


# ---------------------------------------------------------------- drawing field annotations

FIT = re.compile(
    rf"(?P<prefix>Ø|⌀|R|M|М|□)?\s?(?P<nom>{NUM})\s?(?P<fit>(?:[A-HJ-NP-Za-hj-np-z]|J[Ss]|j[s])\d{{1,2}})(?![\w])"
)
THREAD = re.compile(rf"(?P<prefix>M|М)(?P<nom>{NUM})(?:\s?[×xх]\s?(?P<pitch>{NUM}))?\s?-\s?(?P<cls>\d[A-Za-z]{{1,2}})")
PLUS_MINUS = re.compile(rf"(?P<prefix>Ø|⌀)?\s?(?P<nom>{NUM})\s?±\s?(?P<dev>{NUM})")
GDT_SYMBOLS = {
    "⊥": "Допуск перпендикулярности",
    "∥": "Допуск параллельности",
    "//": "Допуск параллельности",
    "◎": "Допуск соосности",
    "↗": "Допуск биения",
    "⏥": "Допуск плоскостности",
    "▱": "Допуск плоскостности",
    "⌖": "Позиционный допуск",
    "⊕": "Позиционный допуск",
    "○": "Допуск круглости",
    "⌭": "Допуск цилиндричности",
    "∠": "Допуск наклона",
    "⌯": "Допуск симметричности",
    "≡": "Допуск симметричности",
}
GDT = re.compile(
    "(?P<sym>" + "|".join(re.escape(s) for s in GDT_SYMBOLS) + rf")\s?(?P<dia>Ø|⌀)?\s?(?P<v>{NUM})"
    r"(?:\s+(?P<datum>[А-ЯA-Z](?:\s?-\s?[А-ЯA-Z])?))?"
)
GENERAL_ROUGHNESS = re.compile(r"\(\s?[√VУ]\s?\)")


def _roughness(m: re.Match[str], text: str, src: SourceRef | None, next_id: NextId, general: bool) -> Parameter:
    letter = m["p"][1].lower()
    param = "Rz" if letter in ("z", "г") else "Ra"
    value = _f(m["v"])
    return Parameter(
        id=next_id(),
        category="roughness",
        name=f"{param} {m['v'].replace('.', ',')}",
        text=text,
        value=value,
        unit="мкм",
        qualifier=param,
        general=general,
        source=src,
        confidence=src.confidence if src else None,
    )


def it_grade(fit: str) -> int | None:
    m = re.search(r"\d{1,2}$", fit)
    return int(m.group(0)) if m else None


def parse_annotation(
    doc: LoadedDocument, page: Page, line: Line, next_id: NextId, *, general_zone: bool
) -> list[Parameter]:
    text = line.text.strip()
    src = doc.source(page, line.bbox, text, line.confidence)
    params: list[Parameter] = []
    taken: list[tuple[int, int]] = []

    def free(m: re.Match[str]) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    for m in THREAD.finditer(text):
        taken.append(m.span())
        name = m.group(0).replace(" ", "")
        params.append(
            Parameter(
                id=next_id(),
                category="dimension_tolerance",
                name=name,
                text=text,
                value=_f(m["nom"]),
                unit="мм",
                qualifier=m["cls"],
                source=src,
                confidence=src.confidence,
            )
        )
    for m in GDT.finditer(text):
        taken.append(m.span())
        name = GDT_SYMBOLS[m["sym"]]
        datum = (m["datum"] or "").replace(" ", "") or None
        params.append(
            Parameter(
                id=next_id(),
                category="geometric",
                name=f"{m['sym']} {name.split(' ', 1)[-1]}",
                text=text,
                value=_f(m["v"]),
                unit="мм",
                qualifier=datum,
                source=src,
                confidence=src.confidence,
            )
        )
    for m in ROUGHNESS.finditer(text):
        if free(m):
            taken.append(m.span())
            general = general_zone or bool(GENERAL_ROUGHNESS.search(text))
            params.append(_roughness(m, text, src, next_id, general=general))
    for m in FIT.finditer(text):
        if not free(m):
            continue
        taken.append(m.span())
        prefix = (m["prefix"] or "").replace("⌀", "Ø")
        name = f"{prefix}{m['nom']}{m['fit']}"
        params.append(
            Parameter(
                id=next_id(),
                category="dimension_tolerance",
                name=name,
                text=text,
                value=_f(m["nom"]),
                unit="мм",
                qualifier=m["fit"],
                it_grade=it_grade(m["fit"]),
                source=src,
                confidence=src.confidence,
            )
        )
    for m in PLUS_MINUS.finditer(text):
        if not free(m):
            continue
        taken.append(m.span())
        nom, dev = _f(m["nom"]), _f(m["dev"])
        params.append(
            Parameter(
                id=next_id(),
                category="dimension_tolerance",
                name=m.group(0).replace(" ", ""),
                text=text,
                value=nom,
                value_min=(nom - dev) if nom is not None and dev is not None else None,
                value_max=(nom + dev) if nom is not None and dev is not None else None,
                unit="мм",
                qualifier=f"±{m['dev']}",
                source=src,
                confidence=src.confidence,
            )
        )
    return params
