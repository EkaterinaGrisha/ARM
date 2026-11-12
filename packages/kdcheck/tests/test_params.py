"""Block 2: parameters from requirement texts and drawing annotations, and criticality rules."""

from itertools import count
from pathlib import Path

import pytest
from kdcheck.config import load_settings
from kdcheck.ingest.document import LoadedDocument, Page
from kdcheck.ingest.text import Line
from kdcheck.params import critical, parameters
from kdcheck.schemas import DocumentInfo, Extracted, Parameter, PartRecord, SourceRef

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
    ("text", "grade", "qualifier"),
    [
        ("Общие допуски по ГОСТ 30893.1: H14, h14, ±IT14/2.", 14, "H14, h14, ±IT14/2"),
        ("Общие допуски по ГОСТ 30893.1-2002: H14, h14, ± IT14 / 2.", 14, "H14, h14, ±IT14/2"),
        ("Общие допуски по ГОСТ 30893.1: H12, h12, ±t1/2.", 12, "H12, h12, ±t1/2"),
        ("Общие допуски по ГОСТ 30893.1-2002: h14, ±", 14, "h14"),
        ("Общие допуски по ГОСТ 30893.1-m.", 14, None),
        ("Неуказанные предельные отклонения размеров: отверстий H12, валов h12, остальных ±IT12/2.", 12, None),
    ],
)
def test_general_tolerances_of_gost_25670_and_30893_1(text, grade, qualifier):
    """GOST 2.307-2011, table 1: the grade of «±IT14/2», of «H14, h14», of the class «t2» of GOST 25670 or «m» of GOST
    30893.1."""
    [param] = requirement(text)
    assert (param.category, param.name, param.it_grade, param.general) == (
        "general_tolerance",
        f"IT{grade}",
        grade,
        True,
    )
    if qualifier is not None:
        assert param.qualifier == qualifier


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Общие допуски формы и расположения — по ГОСТ 30893.2 — К.", [("geometric", "K")]),
        ("Общие допуски по ГОСТ 30893.2-mK.", [("general_tolerance", None), ("geometric", "K")]),
        ("Неуказанные допуски формы и расположения — по ГОСТ 30893.2-2002 — H.", [("geometric", "H")]),
    ],
)
def test_general_tolerances_of_form_and_position_of_gost_30893_2(text, expected):
    """GOST 30893.2: the class H, K or L; «mK» also names the class of the sizes, m of GOST 30893.1."""
    params = requirement(text)
    assert [(p.category, p.qualifier if p.category == "geometric" else None) for p in params] == expected
    assert all(p.general for p in params)
    assert [p.it_grade for p in params if p.category == "general_tolerance"] in ([], [14])


def test_an_item_joined_by_ocr_keeps_the_heat_treatment_after_a_general_tolerance():
    text = "Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2. Закалить ТВЧ h 2...3 мм, 48...52 HRC."
    assert [p.category for p in requirement(text)] == ["general_tolerance", "heat_treatment", "hardness"]


def test_carbonitriding_is_not_carburizing():
    [treatment, _] = requirement("Нитроцементировать h 0,3...0,5 мм, 58...62 HRC.")
    assert (treatment.name, treatment.value_min, treatment.value_max) == ("Нитроцементация", 0.3, 0.5)


@pytest.mark.parametrize(
    ("text", "method", "depth"),
    [
        ("Зубья закалить ТВЧ В 2,0...3,0 мм, 45...50 HRC.", "ocr", (2.0, 3.0)),
        ("Цементировать В 0,8. 1,2 мм. Закалить, отпустить: 58...62 HRC.", "ocr", (0.8, 1.2)),
        ("Закалить ТВЧ h 1,5..2,0 mm, 48...52 HRC.", "ocr", (1.5, 2.0)),
        ("Зубья закалить ТВЧ В 2,0...3,0 мм, 45...50 HRC.", "text_layer", (None, None)),
    ],
)
def test_the_depth_of_a_layer_as_ocr_reads_it(text, method, depth):
    """OCR reads «h» as «В» or «Н» and the dots as «..» or «. »; a text layer writes «h», so there a «В» is a
    surface."""
    item = Extracted(value=text, raw=text, source=SourceRef(document_id="d1", method=method))
    treatment = next(p for p in PARSER.requirement(item, _next_id()) if p.category == "heat_treatment")
    assert (treatment.value_min, treatment.value_max) == depth


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Ø40h6", [("dimension_tolerance", "Ø40h6", 40.0, None, None, 6)]),
        ("Ra 0,8", [("roughness", "Ra 0,8", 0.8, None, None, None)]),
        ("⊥ 0,02 А", [("geometric", "⊥ перпендикулярности", 0.02, None, None, None)]),
        ("М48×1,5-6H", [("dimension_tolerance", "М48×1,5-6H", 48.0, None, None, None)]),
        ("Ø50±0,1", [("dimension_tolerance", "Ø50±0,1", 50.0, 49.9, 50.1, None)]),
        ("120", []),
        ("Ø12js6", [("dimension_tolerance", "Ø12js6", 12.0, None, None, 6)]),
        ("5×45°", []),  # a chamfer
        ("6x45", []),  # a chamfer read by OCR without its degree
        ("5x4", []),
        ("Ø40h45", []),  # no grade 45
    ],
)
def test_annotation_parameters(text, expected):
    assert summary(annotation(text)) == expected


def test_a_size_with_a_tolerance_field_gets_its_limit_deviations():
    """Ø40H7 → 0/+0,025 by GOST 25346; a size with «±» gets its own; a thread gets none."""
    parser = parameters.Parser(SETTINGS.dictionaries, SETTINGS.tolerances)
    line = Line(text="Ø40H7  Ø40k6  Ø50±0,1  М20-6g", bbox=(100, 100, 200, 110), size=10)
    found = {p.name: p for p in parser.annotation(DOC, PAGE, line, _next_id(), general_zone=False)}
    hole, shaft, free = found["Ø40H7"], found["Ø40k6"], found["Ø50±0,1"]
    assert (hole.lower_deviation, hole.upper_deviation, hole.value_min, hole.value_max) == (0.0, 0.025, 40.0, 40.025)
    assert (shaft.lower_deviation, shaft.upper_deviation, shaft.value_min, shaft.value_max) == (
        0.002,
        0.018,
        40.002,
        40.018,
    )
    assert (free.lower_deviation, free.upper_deviation) == (-0.1, 0.1)
    assert found["М20-6g"].upper_deviation is None


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


def test_the_tight_end_of_the_kit_is_marked_when_no_threshold_is_crossed():
    """Nine parts at Ra 6,3 and one at Ra 1,6: no part crosses Ra 0,8, yet one is finer than the rest of the kit."""
    parts = [_part("Ra 6,3") for _ in range(9)] + [_part("Ra 1,6")]
    [tail] = critical.select(parts, SETTINGS.criticality)
    assert (tail.rule, tail.text, tail.score) == ("kit_tail", "Ra 1,6", SETTINGS.criticality.kit_tail.score)
    assert "медиана Ra 6,3" in tail.reason and "жёстче 90%" in tail.reason


def test_no_tail_in_a_small_kit_or_in_a_kit_of_one_value():
    assert critical.select([_part("Ra 6,3") for _ in range(8)] + [_part("Ra 1,6")], SETTINGS.criticality) == []
    assert critical.select([_part("Ra 3,2") for _ in range(12)], SETTINGS.criticality) == []


def test_a_zero_tolerance_and_a_general_grade_are_selected_with_nothing_to_compare_them_with():
    """A positional tolerance «Ø0 Ⓜ» is a value, and the tightest; a general tolerance is no value of the kit: neither
    stops the selection, and neither is compared with values the kit does not have."""
    zero = Parameter(id="p1", category="geometric", name="⌖ Ø0 Ⓜ", text="⌖ Ø0 Ⓜ А", value=0.0)
    general = Parameter(
        id="p2", category="dimension_tolerance", name="h6", text="Неуказанные отклонения h6", it_grade=6, general=True
    )
    selected = critical.select([PartRecord(document_id="d1", parameters=[zero, general])], SETTINGS.criticality)
    assert sorted(c.rule for c in selected) == ["tight_fit", "tight_geometric"]
    assert all("медиана" not in c.reason for c in selected)


def test_a_tolerance_is_compared_with_the_others_of_the_kit():
    tolerances = [Parameter(id=f"p{v}", category="geometric", name="⌯", text="⌯", value=v) for v in (0.01, 0.1, 0.2)]
    [tight] = critical.select([PartRecord(document_id="d1", parameters=tolerances)], SETTINGS.criticality)
    assert "жёстче 67% допусков формы и расположения комплекта (медиана 0,1 мм)" in tight.reason


def test_the_tail_of_hardness_is_its_high_end():
    texts = ["Закалить до 28...32 HRC."] * 9 + ["Закалить до 40...44 HRC."]
    parts = [PartRecord(document_id=f"d{i}", parameters=requirement(t)) for i, t in enumerate(texts)]
    [tail] = critical.select(parts, SETTINGS.criticality)
    assert (tail.rule, tail.document_id) == ("kit_tail", "d9")
