"""Parameters from technical requirements and drawing field annotations.

Results share one :class:`Parameter` structure (category, name, numeric values, units, source text and location)
that compares across parts and loads into accounting systems. Terms (heat treatments, tolerances, form and position
symbols, coatings) come from ``dictionaries.yaml``; the grammars of values are the regular expressions below.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from kdcheck.config import Dictionaries, Tolerances
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import Line
from kdcheck.params.tolerances import PLACES, deviations
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


HARDNESS = re.compile(
    rf"(?P<a>{NUM})\s?(?:\.{{2,3}}|…|-|–)\s?(?P<b>{NUM})\s?(?P<u>HRC[эЭ]?|HB|HV|НВ|НRС)"
    rf"|(?P<u2>HRC[эЭ]?|HB|HV|НВ)\s?(?P<c>{NUM})\s?(?:\.{{2,3}}|…|-|–)\s?(?P<d>{NUM})"
)
RANGE = r"(?:\.{2,3}|…|\.\s|-|–)"  # «0,8...1,2»; OCR also reads the dots as «..» or «. »
IT = re.compile(r"IT\s?(\d{1,2})")
HOLE_SHAFT = re.compile(r"(?<![A-Za-z])[Hh]\s?(\d{1,2})\b")  # «H14, h14» of unspecified limit deviations
CLASS_HALF = re.compile(r"±\s?(?P<cls>t\s?\d)\s?/\s?2")  # «±t2/2»: a class of GOST 25670 for the rest
GENERAL_FITS = re.compile(r"\b[HhJjs]{1,2}\d{1,2}\b|±\s?(?:IT\s?\d{1,2}|t\s?\d)\s?/\s?2")
ROUGHNESS = re.compile(rf"(?<![A-Za-zА-Яа-я])(?P<p>[RРВК][aаzг])\s?(?P<v>{NUM})")


# ---------------------------------------------------------------- drawing field annotations

# A tolerance field of GOST 25346: the letters of its fundamental deviations, I, L, O, Q and W never among them, and
# no X either, whose look is that of «×» in a chamfer «5×45°» or a count «4×Ø10»; a grade IT1–IT18.
FIT = re.compile(
    rf"(?P<prefix>Ø|⌀|R|M|М|□)?\s?(?P<nom>{NUM})\s?"
    r"(?P<fit>(?:J[Ss]|js|[A-HJKMNPR-VYZ]|[a-hjkmnpr-vyz])(?:1[0-8]|[1-9]))(?![\w°])"
)
THREAD = re.compile(rf"(?P<prefix>M|М)(?P<nom>{NUM})(?:\s?[×xх]\s?(?P<pitch>{NUM}))?\s?-\s?(?P<cls>\d[A-Za-z]{{1,2}})")
PLUS_MINUS = re.compile(rf"(?P<prefix>Ø|⌀)?\s?(?P<nom>{NUM})\s?±\s?(?P<dev>{NUM})")
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


def _size(nominal: float | None, deviation: float) -> float | None:
    """A limit size: the nominal one with a deviation, to the micrometre as the deviation has it."""
    return None if nominal is None else round(nominal + deviation, PLACES)


def it_grade(fit: str) -> int | None:
    m = re.search(r"\d{1,2}$", fit)
    return int(m.group(0)) if m else None


class Parser:
    """Parameters from requirement texts and drawing annotations, with the terms of ``words``; a size with a tolerance
    field gets its limit deviations from ``tolerances`` (GOST 25346)."""

    def __init__(self, words: Dictionaries, tolerances: Tolerances | None = None) -> None:
        self._tolerances = tolerances
        self._heat_treatments = [(re.compile(t.pattern, re.IGNORECASE), t.name) for t in words.heat_treatments]
        marks = "".join(re.escape(m) for m in words.layer_depth_marks)
        layer = rf"\s?(?P<a>{NUM})\s?{RANGE}\s?(?P<b>{NUM})\s?(?:мм|mm)"
        self._depth = re.compile(rf"(?<![\wА-Яа-я])h{layer}")
        self._depth_ocr = re.compile(rf"(?<![\wА-Яа-я])[{marks}]{layer}")
        general = words.general_tolerances
        self._general = re.compile("|".join(f"(?:{p})" for p in general.patterns), re.IGNORECASE)
        self._classes = {k.lower(): v for k, v in general.classes.items()}
        letters = "".join(k for k in self._classes if len(k) == 1)
        self._linear_class = re.compile(rf"30893[.,]\s?1(?:\s?-\s?\d{{4}})?\s?-\s?(?P<cls>[{letters}])(?![A-Za-z])")
        geo = words.general_geometric
        self._geo_name, self._geo_classes = geo.name, geo.classes
        cls = "".join(geo.classes) + "НК"  # the Cyrillic look of H and K
        self._general_geometric = re.compile(
            geo.pattern + rf"(?:\s?-\s?\d{{4}})?\s*[-—–]?\s*(?P<lin>[{letters}])?(?P<cls>[{cls}])(?![\wА-Яа-я])"
        )
        self._geometric = [(re.compile(t.pattern), t.name) for t in words.geometric_tolerances]  # on lower case
        self._coating = re.compile("|".join(words.coating_hints), re.IGNORECASE)
        self._gdt_names = dict(words.gdt_symbols)
        self._gdt = re.compile(
            "(?P<sym>" + "|".join(re.escape(s) for s in words.gdt_symbols) + rf")\s?(?P<dia>Ø|⌀)?\s?(?P<v>{NUM})"
            r"(?:\s+(?P<datum>[А-ЯA-Z](?:\s?-\s?[А-ЯA-Z])?))?"
        )

    def requirement(self, item: Extracted, next_id: NextId) -> list[Parameter]:
        """Parameters of one technical requirement item."""
        text = str(item.value or "")
        low = text.lower()
        params: list[Parameter] = []
        src = item.source

        def add(**kw: Any) -> None:
            params.append(
                Parameter(id=next_id(), text=text, source=src, confidence=src.confidence if src else None, **kw)
            )

        geometric = self._general_geometric.search(text)
        if self._general.search(text) or geometric:
            grade = self._general_grade(text, geometric)
            if grade is not None or not geometric:
                fits = [f.replace(" ", "") for f in GENERAL_FITS.findall(text)]
                add(
                    category="general_tolerance",
                    name=f"IT{grade}" if grade else "Неуказанные отклонения",
                    it_grade=grade,
                    qualifier=", ".join(fits) or None,
                    general=True,
                )
            if geometric:
                level = geometric["cls"].translate(str.maketrans("НК", "HK"))
                add(category="geometric", name=self._geo_name, qualifier=level, general=True)
        # OCR joins neighbouring items, so a general tolerance does not end the item
        depth = self._depth_ocr if src is not None and src.method == "ocr" else self._depth
        for pattern, name in self._heat_treatments:
            if pattern.search(text):
                d = depth.search(text)
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
        if self._coating.search(text):
            coat = re.split(r"(?i)покрытие[^:]*?:|покрытие", text, maxsplit=1)
            value = (coat[1] if len(coat) > 1 else text).strip(" .:")
            add(category="coating", name=value[:80], qualifier=value)
        for pattern, name in self._geometric:
            if pattern.search(low):
                tol = re.search(rf"(?:Ø|⌀)?\s?(?P<v>{NUM})\s?мм", text)
                add(category="geometric", name=name, value=_f(tol["v"]) if tol else None, unit="мм" if tol else None)
                break
        for m in ROUGHNESS.finditer(text):
            params.append(_roughness(m, text, src, next_id, general=False))
        return params

    def _fit_limits(self, fit: str, nominal: float | None, prefix: str) -> tuple[float, float] | None:
        """The deviations of a smooth size; a thread «М…» and a radius take none of GOST 25346."""
        if self._tolerances is None or nominal is None or prefix in ("M", "М", "R"):
            return None
        return deviations(fit, nominal, self._tolerances)

    def _general_grade(self, text: str, geometric: re.Match[str] | None) -> int | None:
        """The IT grade of the sizes without limit deviations of their own: «±IT14/2», the grade of «H14, h14», the
        class of GOST 25670 in «±t2/2», or the class of GOST 30893.1, «ГОСТ 30893.1-m» or «ГОСТ 30893.2-mK»."""
        if grade := IT.search(text):
            return int(grade.group(1))
        if grades := {int(g) for g in HOLE_SHAFT.findall(text)}:
            return grades.pop() if len(grades) == 1 else None
        if half := CLASS_HALF.search(text):
            return self._classes.get(half["cls"].replace(" ", "").lower())
        if linear := self._linear_class.search(text):
            return self._classes.get(linear["cls"].lower())
        if geometric is not None and geometric["lin"]:
            return self._classes.get(geometric["lin"].lower())
        return None

    def annotation(
        self, doc: LoadedDocument, page: Page, line: Line, next_id: NextId, *, general_zone: bool
    ) -> list[Parameter]:
        """Parameters of one text line on the drawing field."""
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
        for m in self._gdt.finditer(text):
            taken.append(m.span())
            name = self._gdt_names[m["sym"]]
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
            nom = _f(m["nom"])
            limits = self._fit_limits(m["fit"], nom, prefix)
            params.append(
                Parameter(
                    id=next_id(),
                    category="dimension_tolerance",
                    name=name,
                    text=text,
                    value=nom,
                    value_min=_size(nom, limits[0]) if limits else None,
                    value_max=_size(nom, limits[1]) if limits else None,
                    unit="мм",
                    qualifier=m["fit"],
                    it_grade=it_grade(m["fit"]),
                    lower_deviation=limits[0] if limits else None,
                    upper_deviation=limits[1] if limits else None,
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
                    lower_deviation=-dev if dev is not None else None,
                    upper_deviation=dev,
                    unit="мм",
                    qualifier=f"±{m['dev']}",
                    source=src,
                    confidence=src.confidence,
                )
            )
        return params
