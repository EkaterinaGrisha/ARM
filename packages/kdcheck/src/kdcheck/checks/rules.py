"""Block 1 rules: the catalog of what the completeness check reports.

Each rule has an id in the ``B1.NAME.001`` form, the discrepancy type it reports and the title the report shows.
Its logic is in :mod:`kdcheck.checks.completeness`, a case where it fires and a near miss where it stays quiet are in
the tests. ``configs/checks.yaml`` turns each rule on or off and gives the severity of its discrepancies (ADR-0012).
"""

from __future__ import annotations

from dataclasses import dataclass

from kdcheck.config import Checks, ConfigError, RuleSettings
from kdcheck.schemas import DiscrepancyType


@dataclass(frozen=True)
class Rule:
    id: str
    type: DiscrepancyType
    title: str


RULES: tuple[Rule, ...] = (
    Rule("B1.MISSING.001", "missing_drawing", "Чертёж детали отсутствует"),
    Rule("B1.MISSING.002", "missing_drawing", "Документ отсутствует"),
    Rule("B1.SPEC.001", "missing_specification", "Нет спецификации сборочной единицы"),
    Rule("B1.ORPHAN.001", "orphan_drawing", "Чертёж не входит ни в одну спецификацию"),
    Rule("B1.NAME.001", "name_mismatch", "Наименование на чертеже не совпадает со спецификацией"),
    Rule("B1.NAME.002", "name_mismatch", "Наименование спецификации не совпадает с позицией"),
    Rule("B1.DUP.001", "duplicate_designation", "Обозначение встречается в нескольких документах"),
    Rule("B1.DUP.002", "duplicate_designation", "Одно обозначение у разных позиций"),
    Rule("B1.DUP.003", "duplicate_designation", "Несколько спецификаций с одним обозначением"),
    Rule("B1.VERSION.001", "revision_mismatch", "Разные версии одного документа"),
    Rule("B1.LITERA.001", "revision_mismatch", "Литера спецификации и сборочного чертежа различается"),
    Rule("B1.LITERA.002", "revision_mismatch", "Литера составной части ниже литеры изделия"),
)


@dataclass(frozen=True)
class ActiveRule:
    rule: Rule
    settings: RuleSettings


def configured(checks: Checks) -> dict[str, ActiveRule]:
    """Each rule with its settings from ``checks.yaml``; a rule missing there or unknown here stops the run."""
    known = {rule.id for rule in RULES}
    problems = [f"нет настроек правила {rule.id}" for rule in RULES if rule.id not in checks.rules]
    problems += [f"неизвестное правило {rule_id}" for rule_id in checks.rules if rule_id not in known]
    if problems:
        raise ConfigError("Настройки не прошли проверку:\n" + "\n".join(f"checks.yaml: rules: {p}" for p in problems))
    return {rule.id: ActiveRule(rule, checks.rules[rule.id]) for rule in RULES}
