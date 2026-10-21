"""Block 2: parameters from requirement texts and drawing annotations, and criticality rules."""

from itertools import count
from pathlib import Path

import pytest
from kdcheck.config import load_settings
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import Line
from kdcheck.params import critical, parameters
from kdcheck.schemas import DocumentInfo, Extracted, PartRecord

SETTINGS = load_settings(use_ocr=False)
PARSER = parameters.Parser(SETTINGS.dictionaries)
DOC = LoadedDocument(info=DocumentInfo(id="d1", file_name="a.pdf", path="a.pdf"), abs_path=Path("a.pdf"))
PAGE = Page(number=1, width=1000, height=700, method="text_layer")


def _next_id():
    ids = count(1)
    return lambda: f"p{next(ids)}"


def requirement(text: str):
    return PARSER.requirement(Extracted(value=text, raw=text), _next_id())


def annotation(text: str):
    line = Line(text=text, bbox=(100, 100, 200, 110), size=10)
    return PARSER.annotation(DOC, PAGE, line, _next_id(), general_zone=False)


def summary(params):
    return [(p.category, p.name, p.value, p.value_min, p.value_max, p.it_grade) for p in params]


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2.",
            [("general_tolerance", "IT14", None, None, None, 14)],
        ),
        (
            "Цементировать h 0,8...1,2 мм, 58...62 HRC.",
            [
                ("heat_treatment", "Цементация", None, 0.8, 1.2, None),
                ("hardness", "Твёрдость HRC", None, 58.0, 62.0, None),
            ],
        ),
        ("Покрытие Ц9.хр.", [("coating", "Ц9.хр", None, None, None, None)]),
        ("Позиционный допуск осей отверстий Ø0,02 мм.", [("geometric", "Позиционный допуск", 0.02, None, None, None)]),
        ("Размеры для справок.", []),
        ("Острые кромки притупить R0,5.", []),
    ],
)
def test_requirement_parameters(text, expected):
    assert summary(requirement(text)) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Ø40h6", [("dimension_tolerance", "Ø40h6", 40.0, None, None, 6)]),
        ("Ra 0,8", [("roughness", "Ra 0,8", 0.8, None, None, None)]),
        ("⊥ 0,02 А", [("geometric", "⊥ перпендикулярности", 0.02, None, None, None)]),
        ("М48×1,5-6H", [("dimension_tolerance", "М48×1,5-6H", 48.0, None, None, None)]),
        ("Ø50±0,1", [("dimension_tolerance", "Ø50±0,1", 50.0, 49.9, 50.1, None)]),
        ("120", []),
    ],
)
def test_annotation_parameters(text, expected):
    assert summary(annotation(text)) == expected


def _part(*texts: str) -> PartRecord:
    params = [p for t in texts for p in annotation(t)]
    return PartRecord(document_id="d1", designation="АРМ.303411.001", name="Вал", parameters=params)


def test_critical_rules_fire_on_tight_values_only():
    selected = critical.select([_part("Ø40h6", "Ø40h12", "Ra 0,8", "Ra 6,3")], SETTINGS.criticality)
    assert sorted((c.rule, c.text) for c in selected) == [("fine_roughness", "Ra 0,8"), ("tight_fit", "Ø40h6")]
    assert all(c.reason and c.source for c in selected)


def test_no_critical_parameters_in_a_loose_part():
    assert critical.select([_part("Ø40h12", "Ra 6,3", "Ra 12,5")], SETTINGS.criticality) == []


def test_criticality_thresholds_come_from_the_settings():
    rules = SETTINGS.criticality
    looser = rules.model_copy(update={"tight_fit": rules.tight_fit.model_copy(update={"max_it_grade": 12})})
    selected = critical.select([_part("Ø40h12")], looser)
    assert [(c.rule, c.text, c.score) for c in selected] == [("tight_fit", "Ø40h12", rules.tight_fit.score)]
