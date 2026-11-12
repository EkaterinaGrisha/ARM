"""Parameters that are potentially critical for production.

The criteria combine general engineering thresholds (tight IT grades, low roughness, high hardness, chemical heat
treatment, tight form tolerances) with the distribution of values inside the doc set: "the tightest in the set" and
"only one part has it" signal the technologist even when no absolute threshold is crossed. A value at the tight end of
the kit, the share ``kit_tail.share`` of its values, is marked too, when it is tighter than the median: a kit of rough
parts has its own tail. The thresholds and scores are in ``criticality.yaml``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median

from kdcheck.config import Criticality, KitTail
from kdcheck.schemas import CriticalParameter, Parameter, PartRecord


@dataclass(frozen=True)
class _Feature:
    """A feature of the kit whose tail is marked: how a parameter gives it, and which end is the tight one."""

    title: str
    value: Callable[[Parameter], float | None]
    tight_low: bool  # a lower value is tighter
    shown: Callable[[float], str]


def _grade(p: Parameter) -> float | None:
    return p.it_grade if p.category == "dimension_tolerance" and not p.general else None


def _ra(p: Parameter) -> float | None:
    return p.value if p.category == "roughness" and p.qualifier == "Ra" and not p.general else None


def _form(p: Parameter) -> float | None:
    return p.value if p.category == "geometric" and not p.general else None


def _hrc(p: Parameter) -> float | None:
    return p.value_min if p.category == "hardness" and (p.unit or "").startswith("HRC") else None


FEATURES = (
    _Feature("квалитет размера", _grade, True, lambda v: f"IT{v:g}"),
    _Feature("шероховатость", _ra, True, lambda v: f"Ra {_fmt(v)} мкм"),
    _Feature("допуск формы и расположения", _form, True, lambda v: f"{_fmt(v)} мм"),
    _Feature("твёрдость", _hrc, False, lambda v: f"{_fmt(v)} HRC"),
)


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:g}".replace(".", ",")


def _against(value: float, kit: Sequence[float], shown: Callable[[float], str], what: str) -> str:
    """How the value stands among the values of its kind in the kit, «; точнее 80% … (медиана …)»; nothing when the
    kit has no other value to compare with, as for a general tolerance, which the kit values leave out."""
    if len(kit) < 2:
        return ""
    share = sum(1 for v in kit if v > value) / len(kit)
    return f"; {what.format(share=f'{share:.0%}')} комплекта (медиана {shown(median(kit))})"


def select(parts: list[PartRecord], rules: Criticality) -> list[CriticalParameter]:
    fit, rough, hard = rules.tight_fit, rules.fine_roughness, rules.high_hardness
    chem, geo, rare = rules.chemical_heat_treatment, rules.tight_geometric, rules.rare_in_kit
    result: list[CriticalParameter] = []
    all_params = [(part, p) for part in parts for p in part.parameters]

    grades = [p.it_grade for _, p in all_params if p.category == "dimension_tolerance" and p.it_grade and not p.general]
    ra = [p.value for _, p in all_params if p.category == "roughness" and p.qualifier == "Ra" and p.value]
    gdt = [p.value for _, p in all_params if p.category == "geometric" and p.value is not None]  # «Ø0 Ⓜ» is a value
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
            add(
                part,
                p,
                "tight_fit",
                f"Квалитет IT{p.it_grade} ({p.qualifier}) — требует чистовой обработки (шлифование) и контроля "
                f"калибрами{_against(p.it_grade, grades, lambda v: f'IT{v:g}', 'точнее {share} посадочных размеров')}",
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
                f"Допуск {_fmt(p.value)} мм — обработка с одной установки или на координатном оборудовании"
                + _against(p.value, gdt, lambda v: f"{_fmt(v)} мм", "жёстче {share} допусков формы и расположения"),
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
    _tail(parts, rules.kit_tail, add)
    result.sort(key=lambda c: -c.score)
    for i, c in enumerate(result, start=1):
        c.id = f"c{i}"
    return result


def _tail(
    parts: list[PartRecord], rules: KitTail, add: Callable[[PartRecord, Parameter, str, str, float], None]
) -> None:
    """Values at the tight end of the kit, of a feature with at least ``min_values`` values, beyond the median."""
    for feature in FEATURES:
        values = [(part, p, v) for part in parts for p in part.parameters if (v := feature.value(p)) is not None]
        if len(values) < rules.min_values:
            continue
        ordered = sorted(v for _, _, v in values)
        if not feature.tight_low:
            ordered.reverse()
        edge, middle = ordered[int(rules.share * (len(ordered) - 1))], median(ordered)
        for part, p, v in values:
            tight = v <= edge and v < middle if feature.tight_low else v >= edge and v > middle
            if not tight or p.critical:
                continue
            beyond = sum(1 for o in ordered if (o > v if feature.tight_low else o < v)) / len(ordered)
            add(
                part,
                p,
                "kit_tail",
                f"{feature.title.capitalize()} {feature.shown(v)} — в «хвосте» комплекта: жёстче {beyond:.0%} из "
                f"{len(ordered)} значений (медиана {feature.shown(middle)}); порогов общих правил не переходит, но "
                "у этой детали требование строже, чем у остальных",
                rules.score,
            )
