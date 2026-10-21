"""Parameters that are potentially critical for production.

The criteria combine general engineering thresholds (tight IT grades, low roughness, high hardness, chemical heat
treatment, tight form tolerances) with the distribution of values inside the doc set: "the tightest in the set" and
"only one part has it" signal the technologist even when no absolute threshold is crossed. The thresholds and scores
are in ``criticality.yaml``.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

from kdcheck.config import Criticality
from kdcheck.schemas import CriticalParameter, Parameter, PartRecord


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:g}".replace(".", ",")


def select(parts: list[PartRecord], rules: Criticality) -> list[CriticalParameter]:
    fit, rough, hard = rules.tight_fit, rules.fine_roughness, rules.high_hardness
    chem, geo, rare = rules.chemical_heat_treatment, rules.tight_geometric, rules.rare_in_kit
    result: list[CriticalParameter] = []
    all_params = [(part, p) for part in parts for p in part.parameters]

    grades = [p.it_grade for _, p in all_params if p.category == "dimension_tolerance" and p.it_grade and not p.general]
    ra = [p.value for _, p in all_params if p.category == "roughness" and p.qualifier == "Ra" and p.value]
    gdt = [p.value for _, p in all_params if p.category == "geometric" and p.value]
    coatings = Counter(
        (p.qualifier or p.name).lower()
        for part in parts
        for p in {x.qualifier or x.name: x for x in part.parameters if x.category == "coating"}.values()
    )
    treatments = Counter(
        p.name
        for part in parts
        for p in {x.name: x for x in part.parameters if x.category == "heat_treatment"}.values()
    )

    def add(part: PartRecord, p: Parameter, rule: str, reason: str, score: float) -> None:
        p.critical = True
        result.append(
            CriticalParameter(
                id=f"c{len(result) + 1}",
                document_id=part.document_id,
                designation=part.designation,
                part_name=part.name,
                parameter_id=p.id,
                category=p.category,
                text=p.name if p.category != "coating" else p.text,
                rule=rule,
                reason=reason,
                score=round(score, 2),
                source=p.source,
            )
        )

    for part, p in all_params:
        if p.category == "dimension_tolerance" and p.it_grade and p.it_grade <= fit.max_it_grade:
            share = sum(1 for g in grades if g > p.it_grade) / len(grades) if grades else 0
            add(
                part,
                p,
                "tight_fit",
                f"Квалитет IT{p.it_grade} ({p.qualifier}) — требует чистовой обработки (шлифование) и контроля "
                f"калибрами; точнее {share:.0%} посадочных размеров комплекта (медиана IT{median(grades):g})",
                fit.strict_score if p.it_grade <= fit.strict_it_grade else fit.score,
            )
        elif p.category == "roughness" and p.value is not None and not p.general:
            limit = rough.max_ra if p.qualifier == "Ra" else rough.max_rz
            is_min = p.qualifier == "Ra" and ra and p.value <= min(ra)
            if p.value <= limit:
                add(
                    part,
                    p,
                    "fine_roughness",
                    f"{p.qualifier} {_fmt(p.value)} мкм — финишная обработка (шлифование, хонингование)"
                    + (f"; минимальное значение в комплекте (медиана Ra {_fmt(median(ra))})" if is_min else ""),
                    rough.kit_min_score if is_min else rough.score,
                )
        elif p.category == "hardness" and p.unit and p.unit.startswith("HRC") and (p.value_min or 0) >= hard.min_hrc:
            add(
                part,
                p,
                "high_hardness",
                f"Твёрдость {_fmt(p.value_min)}…{_fmt(p.value_max)} {p.unit} — закалка с последующей "
                "обработкой твёрдых поверхностей, риск коробления",
                hard.strict_score if (p.value_min or 0) >= hard.strict_min_hrc else hard.score,
            )
        elif p.category == "heat_treatment" and p.name in chem.treatments:
            add(
                part,
                p,
                "chemical_heat_treatment",
                f"{p.name}"
                f"{' на глубину ' + _fmt(p.value_min) + '…' + _fmt(p.value_max) + ' мм' if p.value_min else ''} — "
                "длительная операция в специализированной печи, требует защиты незакаливаемых поверхностей "
                f"(в комплекте у {treatments[p.name]} дет.)",
                chem.score,
            )
        elif p.category == "geometric" and p.value is not None and p.value <= geo.max_tolerance:
            add(
                part,
                p,
                "tight_geometric",
                f"Допуск {_fmt(p.value)} мм — обработка с одной установки или на координатном оборудовании; "
                f"жёстче {sum(1 for v in gdt if v > p.value) / len(gdt):.0%} допусков формы и расположения комплекта",
                geo.score,
            )
        elif (
            p.category == "coating" and coatings[(p.qualifier or p.name).lower()] == 1 and len(parts) >= rare.min_parts
        ):
            add(
                part,
                p,
                "rare_in_kit",
                f"Покрытие «{p.qualifier or p.name}» встречается только у этой детали комплекта — "
                "отдельный передел или кооперация, проверить наличие на площадке",
                rare.score,
            )
    result.sort(key=lambda c: -c.score)
    for i, c in enumerate(result, start=1):
        c.id = f"c{i}"
    return result
